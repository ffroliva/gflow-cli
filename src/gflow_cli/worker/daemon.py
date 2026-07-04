from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from gflow_cli._cli_helpers import apply_tool_option
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import ProjectInfo
from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import GenerateImageRequest, ImageRef
from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.video import Aspect as VideoAspect
from gflow_cli.api.video import GenerateVideoRequest, VideoModel, VideoStarted
from gflow_cli.api.video import Mode as VideoMode
from gflow_cli.api.video import Tier as VideoTier
from gflow_cli.config import get_settings
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import GFlowError
from gflow_cli.paths import image_output_path
from gflow_cli.storage import cloud_info_from_path
from gflow_cli.worker.queue import QueueRepository, QueueTask

logger = structlog.get_logger()

_PROFILE_LOCKS: dict[str, asyncio.Lock] = {}


def get_profile_lock(profile_name: str) -> asyncio.Lock:
    if profile_name not in _PROFILE_LOCKS:
        _PROFILE_LOCKS[profile_name] = asyncio.Lock()
    return _PROFILE_LOCKS[profile_name]


class FlowWorker:
    def __init__(self, profile_name: str, db_path: str):
        self.profile_name = profile_name
        self.db_path = Path(db_path)
        self.db = DataStore.open(self.db_path)
        self.repo = QueueRepository(self.db)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def close(self) -> None:
        self.db.close()

    async def start(self) -> None:
        logger.info("Starting FlowWorker", profile_name=self.profile_name)
        while not self._stop:
            try:
                task = self.repo.get_next_pending_task(self.profile_name)
                if task:
                    lock = get_profile_lock(self.profile_name)
                    async with lock:
                        await self.process_task(task)
                else:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                # Cooperative cancellation: log, then re-raise so the framework
                # (and the daemon lifespan awaiting this task) sees the worker
                # has acknowledged the cancellation and is stopping.
                logger.info("FlowWorker loop cancelled", profile_name=self.profile_name)
                raise
            except Exception as exc:
                logger.exception(
                    "Error in FlowWorker loop", profile_name=self.profile_name, exc_info=exc
                )
                await asyncio.sleep(5)

    async def process_task(self, task: QueueTask) -> None:
        logger.info(
            "Processing task",
            task_id=task.task_id,
            task_type=task.task_type,
            profile_name=self.profile_name,
        )
        self.repo.update_task_status(task.task_id, status="processing")

        settings = get_settings()
        profile_dir = settings.profile_subdir(task.profile_name)
        headless = task.payload.get("headless", settings.headless)
        transport = task.payload.get("transport", settings.transport)
        out_dir = (
            Path(task.payload["out_dir"]) if "out_dir" in task.payload else settings.output_dir
        )

        try:
            if task.task_type in ("t2i", "i2i"):
                req = self._build_image_request(task.payload)
                count = task.payload.get("count", 1)
                project_id = task.payload.get("project_id")

                recorder = OperationRecorder(
                    DataRepository(self.db), prompt_mode=settings.history_prompts
                )
                try:
                    async with FlowApiClient(
                        profile_dir=profile_dir,
                        headless=headless,
                        transport=transport,
                        out_dir=out_dir,
                    ) as client:
                        project_title = task.payload.get("project_title", "gflow-cli images")
                        project_created = False
                        if project_id:
                            project_flow_id = project_id
                            project = ProjectInfo(project_id=project_id, title=project_title)
                        else:
                            project = await client.create_project(title=project_title)
                            project_flow_id = project.project_id
                            project_created = True

                        if count == 1:
                            img = await client.generate_image(project_id=project_flow_id, req=req)
                            images = [img]
                        else:
                            images = await client.generate_images_batch(
                                project_id=project_flow_id,
                                req=req,
                                count=count,
                            )

                        flow_media_id = images[0].media_name if images else None

                        saved_paths: list[Path] = []
                        for i, img in enumerate(images, start=1):
                            target = image_output_path(
                                settings.output_dir, job_id=img.media_name, index=i
                            )
                            saved = await client.download_image(img, target)
                            saved_paths.append(saved)

                        recorder.record_generated_images(
                            profile_name=self.profile_name,
                            profile_dir=profile_dir,
                            project=project,
                            project_created=project_created,
                            request=req,
                            images=images,
                            saved_paths=saved_paths,
                            input_media_ids=(
                                [r.name for r in req.refs] if hasattr(req, "refs") else []
                            ),
                            operation_kind=task.task_type,
                            cloud_storage_infos=[cloud_info_from_path(p) for p in saved_paths],
                        )
                except Exception as exc:
                    logger.warning("Failed during image generation or recording", exc_info=exc)
                    raise

                self.repo.update_task_status(
                    task.task_id,
                    status="completed",
                    flow_media_id=flow_media_id,
                )
                logger.info(
                    "Task completed successfully", task_id=task.task_id, flow_media_id=flow_media_id
                )

            elif task.task_type in ("t2v", "i2v", "r2v"):
                req = self._build_video_request(task.payload)
                project_id = task.payload.get("project_id")

                recorder = OperationRecorder(
                    DataRepository(self.db), prompt_mode=settings.history_prompts
                )
                try:
                    async with FlowApiClient(
                        profile_dir=profile_dir,
                        headless=headless,
                        transport=transport,
                        out_dir=out_dir,
                    ) as client:

                        def on_started(started: VideoStarted) -> None:
                            try:
                                recorder.record_started_video(
                                    profile_name=self.profile_name,
                                    profile_dir=profile_dir,
                                    request=req,
                                    started=started,
                                )
                            except Exception as exc:
                                logger.warning("Failed to record started video", exc_info=exc)

                        result = await client.generate_video(
                            req=req,
                            project_id=project_id,
                            out_dir=out_dir,
                            download=True,
                            on_started=on_started,
                        )
                        flow_media_id = result.status.media_id

                    try:
                        recorder.record_completed_video(
                            profile_name=self.profile_name,
                            _profile_dir=profile_dir,
                            request=req,
                            result=result,
                            cloud_storage_info=(
                                cloud_info_from_path(result.local_path)
                                if result.local_path is not None
                                else None
                            ),
                        )
                    except Exception as exc:
                        # Post-success recording must never flip a credit-spent
                        # video to "failed" — warn and continue (cf. exit-code-16
                        # data-store contract, on_started recorder safety).
                        logger.warning("Failed to record completed video", exc_info=exc)

                    if not result.status.succeeded:
                        reasons = (
                            ", ".join(result.status.failure_reasons)
                            if result.status.failure_reasons
                            else result.status.error_message or "Unknown reason"
                        )
                        raise GFlowError(f"Video generation failed: {reasons}")
                except Exception as exc:
                    logger.warning("Failed during video generation or recording", exc_info=exc)
                    raise

                self.repo.update_task_status(
                    task.task_id,
                    status="completed",
                    flow_media_id=flow_media_id,
                )
                logger.info(
                    "Task completed successfully", task_id=task.task_id, flow_media_id=flow_media_id
                )
            else:
                raise ValueError(f"Unknown task type: {task.task_type}")

        except Exception as exc:
            logger.exception(
                "Task execution failed",
                task_id=task.task_id,
                task_type=task.task_type,
                exc_info=exc,
            )

            if isinstance(exc, GFlowError):
                error_payload = dict(exc.to_problem_details())
                from gflow_cli.errors import EXIT_CODE_MAP

                exit_code = next(
                    (code for cls, code in EXIT_CODE_MAP.items() if isinstance(exc, cls)),
                    1,
                )
                error_payload["exit_code"] = exit_code
                if "status" not in error_payload:
                    error_payload["status"] = 500
            else:
                error_payload = {
                    "type": "https://gflow-cli.dev/errors/unknown",
                    "title": "Unknown Error",
                    "status": 500,
                    "detail": str(exc),
                    "exit_code": 1,
                }

            self.repo.update_task_status(
                task.task_id,
                status="failed",
                error=error_payload,
            )

    def _build_image_request(self, payload: dict[str, Any]) -> GenerateImageRequest:
        prompt = payload["prompt"]

        tool_specs = tuple(payload.get("tool_specs", ()))
        if tool_specs:
            # Mirror the CLI --tool flag: expand the prompt before generation.
            prompt = apply_tool_option(prompt, tool_specs, category="image", quiet=True)[0]

        aspect_val = payload.get("aspect")
        aspect = ImageAspect.from_cli(aspect_val) if aspect_val else ImageAspect.PORTRAIT

        model_val = payload.get("model")
        model = ImageModel.from_cli(model_val) if model_val else ImageModel.NARWHAL

        refs = tuple(ImageRef(r) for r in payload.get("refs", []))
        ref_paths = tuple(Path(p) for p in payload.get("ref_paths", []))
        ref_names = tuple(payload.get("ref_names", []))
        reference_entities = tuple(payload.get("reference_entities", []))
        reference_entity_names = tuple(payload.get("reference_entity_names", []))
        count = payload.get("count", 1)

        return GenerateImageRequest(
            prompt=prompt,
            aspect=aspect,
            model=model,
            refs=refs,
            ref_paths=ref_paths,
            ref_names=ref_names,
            reference_entities=reference_entities,
            reference_entity_names=reference_entity_names,
            count=count,
        )

    def _build_video_request(self, payload: dict[str, Any]) -> GenerateVideoRequest:
        prompt = payload["prompt"]

        tool_specs = tuple(payload.get("tool_specs", ()))
        if tool_specs:
            # Mirror the CLI --tool flag: expand the prompt before generation.
            prompt = apply_tool_option(prompt, tool_specs, category="video", quiet=True)[0]

        mode_val = payload.get("mode")
        mode = VideoMode(mode_val) if mode_val else VideoMode.T2V

        aspect_val = payload.get("aspect")
        aspect = VideoAspect.from_cli(aspect_val) if aspect_val else VideoAspect.PORTRAIT

        tier_val = payload.get("tier")
        tier = VideoTier(tier_val) if tier_val else VideoTier.FAST

        model_val = payload.get("model")
        model = VideoModel.from_cli(model_val) if model_val else None

        duration = payload.get("duration")
        count = payload.get("count", 1)
        seed = payload.get("seed")

        start_image = Path(payload["start_image"]) if payload.get("start_image") else None
        start_image_ref_name = payload.get("start_image_ref_name")
        end_image = Path(payload["end_image"]) if payload.get("end_image") else None
        end_image_ref_name = payload.get("end_image_ref_name")
        reference_images = tuple(Path(p) for p in payload.get("reference_images", []))
        ref_names = tuple(payload.get("ref_names", []))
        reference_entities = tuple(payload.get("reference_entities", []))
        reference_entity_names = tuple(payload.get("reference_entity_names", []))
        reference_audio = payload.get("reference_audio")

        return GenerateVideoRequest(
            prompt=prompt,
            mode=mode,
            aspect=aspect,
            tier=tier,
            model=model,
            duration=duration,
            count=count,
            seed=seed,
            start_image=start_image,
            start_image_ref_name=start_image_ref_name,
            end_image=end_image,
            end_image_ref_name=end_image_ref_name,
            reference_images=reference_images,
            ref_names=ref_names,
            reference_entities=reference_entities,
            reference_entity_names=reference_entity_names,
            reference_audio=reference_audio,
        )
