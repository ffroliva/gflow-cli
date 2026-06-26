from __future__ import annotations

from gflow_cli.mcp.server import server


@server.prompt()
def expand_prompt(
    subject: str,
    action: str = "",
    setting: str = "",
    camera: str = "",
    lighting: str = "",
) -> str:
    """Expand a simple prompt concept into Google Flow's official 5-component prompt formula.

    Inputs:
    - subject: The primary subject of the generation.
    - action: Description of actions or motion (optional).
    - setting: Setting/location/background (optional).
    - camera: Camera angle, lens type, framing, movement (optional).
    - lighting: Light source, mood, color palette, atmosphere (optional).
    """
    parts: list[str] = []
    parts.append(f"Subject: {subject}")
    if action:
        parts.append(f"Action/Movement: {action}")
    if setting:
        parts.append(f"Setting/Location: {setting}")
    if camera:
        parts.append(f"Camera/Framing: {camera}")
    if lighting:
        parts.append(f"Lighting/Atmosphere: {lighting}")

    formula = "\n".join(parts)
    return (
        "You are the Creative Director for Google Flow. "
        "Convert this concept into a highly descriptive prompt:\n\n"
        f"{formula}\n\n"
        "Please output a single, detailed prompt paragraph blending "
        "all five components cohesively for generation."
    )


@server.prompt()
def create_character(
    name: str,
    gender: str = "neutral",
    appearance: str = "",
    clothing: str = "",
) -> str:
    """Create a consistent character profile prompt structure for Google Flow character models.

    Inputs:
    - name: The name of the character.
    - gender: Gender profile of the character (default: neutral).
    - appearance: Appearance traits (hair, eyes, facial features) (optional).
    - clothing: Clothing style and colors (optional).
    """
    traits: list[str] = []
    if gender:
        traits.append(f"gender: {gender}")
    if appearance:
        traits.append(f"physical appearance: {appearance}")
    if clothing:
        traits.append(f"clothing style: {clothing}")

    char_details = ", ".join(traits)
    return (
        f"Create a character description prompt for a character named '{name}' "
        f"with details: {char_details}.\n"
        "The prompt must describe the character's facial features, posture, and "
        "clothing in high detail, focused on key identifying attributes to "
        "establish a highly consistent facial and physical profile."
    )
