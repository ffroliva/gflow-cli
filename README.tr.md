<!-- mcp-name: io.github.ffroliva/gflow-cli -->

# gflow-cli: Google Flow (Veo 3.1, Omni, Nano Banana) için MCP sunucusu ve CLI

[English](README.md) | [简体中文](README.zh-CN.md) | **Türkçe**

> **gflow-cli, [Google Flow](https://labs.google/fx/tools/flow) platformunu otomatikleştiren açık kaynaklı bir MCP sunucusu ve komut satırı aracıdır.** Claude Code, Claude Desktop, Cursor, Codex, VS Code veya terminalinizden kendi Google hesabınızın Flow kredilerini kullanarak Veo 3.1 ve Omni videoları ile Nano Banana ve Imagen görselleri üretir. Google Flow CLI veya Veo MCP sunucusu olarak kullanabilirsiniz. PyPI üzerinden `uv tool install gflow-cli` ile kurun (MIT, macOS, Windows ve Linux).

[![PyPI version](https://img.shields.io/pypi/v/gflow-cli.svg)](https://pypi.org/project/gflow-cli/)
[![CI](https://github.com/ffroliva/gflow-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/ffroliva/gflow-cli/actions/workflows/ci.yml)
[![Python versions](https://img.shields.io/pypi/pyversions/gflow-cli.svg)](https://pypi.org/project/gflow-cli/)
[![PyPI downloads](https://static.pepy.tech/badge/gflow-cli/month)](https://pepy.tech/project/gflow-cli)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status: beta](https://img.shields.io/badge/status-beta-blue.svg)](docs/PROJECT_STATUS.md)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=ffroliva_gflow-cli&metric=coverage)](https://sonarcloud.io/component_measures?id=ffroliva_gflow-cli&metric=coverage)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/ffroliva/gflow-cli/badge)](https://scorecard.dev/viewer/?uri=github.com/ffroliva/gflow-cli)
[![Listed on mcpservers.org](https://mcpservers.org/badge.svg)](https://mcpservers.org/servers/ffroliva/gflow-cli)

<!-- Gold sponsors appear here: filled by scripts/ci/update_sponsors.py, empty until there is one. -->
<!-- sponsors-gold:start -->

<!-- sponsors-gold:end -->

Bağımsız bir projedir, Google ile bağlantılı değildir. Gerçek bir Chrome penceresinde kendi oturum açmış Flow oturumunuzu kullanır; bu nedenle hesabınız için Google'ın koşulları geçerlidir: [DISCLAIMER](DISCLAIMER.md) ve [ACCOUNT_SAFETY](docs/ACCOUNT_SAFETY.md) belgelerini okuyun.

## Bir bakışta gflow-cli

| | |
|---|---|
| **Nedir?** | Google Flow için hem CLI (`gflow`) hem de MCP sunucusu (`gflow mcp run`, `gflow serve`) olan tek bir Python paketi |
| **Ajan yüzeyi** | stdio ve Streamable HTTP üzerinden **29 MCP aracı** artı kaynaklar ve istemler; [resmî MCP Kayıt Defterinde](https://registry.modelcontextprotocol.io/) `io.github.ffroliva/gflow-cli` olarak listelenmiştir |
| **Terminal yüzeyi** | Çoğu `--json` çıktılı, tipli çıkış kodlarına sahip 70+ komut; `gflow --help --json` ajanlar için tüm ağacı yazdırır |
| **Video modelleri** | Veo 3.1 Lite, Fast ve Quality; Omni Flash ve Omni 1.2 Flash (15 saniyeye kadar, 7 referans görsele kadar) |
| **Görsel modelleri** | Nano Banana 2.1, Nano Banana Pro, Nano Banana 2 Lite, Imagen 4 |
| **Uyumluluk** | Claude Code (eklenti), Claude Desktop, Cursor, VS Code, Codex, herhangi bir MCP istemcisi, kabuk betikleri ve CI |
| **Maliyet** | Ücretsiz ve MIT lisanslıdır. Video kendi Flow kredilerinizi harcar; görsel üretiminin kredi harcamadığı gözlemlenmiştir (günlük görsel kotasından düşer) |
| **Olgunluk** | Sürümler [değişiklik günlüğünde](https://github.com/ffroliva/gflow-cli/blob/main/CHANGELOG.md) listelenir; v0.54.0'dan beri her sürüm yayımlanmadan önce gerçek Google Flow üzerinde canlı doğrulanır |

## gflow-cli neler yapabilir?

- **Video üretme**: Veo 3.1 ve Omni üzerinde metinden videoya, başlangıç ve bitiş kareli görselden videoya ve referanstan videoya (`gflow video t2v | i2v | r2v`, veya kipi sizin yerinize seçmesi için `gflow video generate`).
- **Uzun videolar yapma**: `gflow video extend --segments N` bir klibi kesintisiz birleştirmeyle Flow'un 8 saniye sınırının ötesine uzatır, `gflow video chain` çekimleri kareden kareye bağlar ve `gflow scene create` klipleri sunucu tarafında ücretsiz birleştirir.
- **Görsel üretme**: metinden görsele ve görselden görsele, çağrı başına 1–4 adet, beş en-boy oranı (`gflow image t2i | i2i`).
- **Karakter tutarlılığını koruma**: `gflow character create` yeniden kullanılabilir bir Flow Karakteri (yüz, vücut, ses) oluşturur ve istemlerde `@Name` olarak anılabilir.
- **Ölçek yükseltme (Upscale)**: Flow'un sunduğu öğelerde videoyu 4K, 1080p veya GIF'e; görselleri 2K'ya (Ultra planda 4K'ya) yükseltir.
- **Toplu işler ve işlem hatları çalıştırma**: bir satırın önceki satır üzerine inşa edilebildiği JSON toplu çalıştırmaları (`gflow run`), bir bildirimden çok sahneli filmler (`gflow movie run`) ve kalıcı Ajan Kipi brifingleri (`gflow instructions`).
- **İstemleri yeniden yazma**: `--tool creative-director` ve diğer iki yerleşik istem aracı (`reverse-engineer`, `storyboard`) veya kendi araçlarınız ([TOOLS](docs/TOOLS.md)).
- **Harcama ve medyayı takip etme**: Veo kredi bakiyesi (`gflow credits`), yerel medya kataloğu (`gflow data`), sağlık kontrolü (`gflow doctor`) ve S3, MinIO veya Google Cloud Storage'a çıktı.

Yoldaki özellikler [#1367](https://github.com/ffroliva/gflow-cli/issues/1367) altında izlenir.

## Claude Code, Cursor veya Codex'i Google Flow'a nasıl bağlarım?

`gflow-cli` kurun, bir kez oturum açın, ardından MCP sunucusunu istemcinize kaydedin.

```bash
uv tool install gflow-cli
gflow auth login --browser chrome
```

| İstemci | Tek adım |
|---|---|
| **Claude Code** | `/plugin marketplace add ffroliva/gflow-cli` ardından `/plugin install gflow@gflow-cli` (MCP sunucusu ile `gflow-cli` ve `video-production` becerilerini ekler) |
| **Claude Desktop** | `gflow mcp setup` |
| **Cursor** | `gflow mcp setup --target cursor` |
| **VS Code** | `gflow mcp setup --target vscode` |
| **Codex** | Beceriler: klondan `codex plugin marketplace add .` ardından `codex plugin add gflow@gflow-cli`; MCP: `gflow mcp run` komutunu stdio sunucusu olarak kaydedin |
| **Herhangi bir MCP istemcisi** | komut `uvx`, argümanlar `gflow-cli mcp run`; veya `/mcp` üzerinde Streamable HTTP için `gflow serve` |
| **OpenAI SDK kodu** | `gflow serve`, ardından `base_url="http://127.0.0.1:8000/v1"`: Flow görsel modelleriyle `POST /v1/images/generations`, `b64_json` yanıtları ([docs/MCP.md](docs/MCP.md#openai-compatible-image-endpoint)) |

Claude Code eklentisi, etkinleştirilmesi Veo kredilerinizi harcayabilecek bir sunucu başlattığı için varsayılan olarak kapalı gelir. Kesin bir güvence için kredi harcayan araçları hiç kaydetmeyen `gflow mcp run --no-spend` komutunu kaydedin. Tam kurulum: [docs/MCP.md](docs/MCP.md).

## 60 saniyelik hızlı başlangıç

**Kurmadan önce** şunlara sahip olduğunuzu kontrol edin:

- Python 3.11 veya üzeri (`uv tool install` bunu indirir) ve [`uv`](https://docs.astral.sh/uv/getting-started/installation/);
- Google Chrome (oturum açma ve üretim gerçek bir Chrome penceresini sürer);
- tek seferlik oturum açma için bir ekran (Linux sunucularında Xvfb);
- [Google'ın Flow sunduğu bir bölgede](https://support.google.com/flow/answer/16353544) Flow erişimine sahip bir Google hesabı.

PyPI, Chromium indirmesi veya Google'a doğrudan erişilemiyorsa önce [Behind a proxy or a package mirror](https://github.com/ffroliva/gflow-cli/blob/develop/docs/USER_GUIDE.md#15-behind-a-proxy-or-a-package-mirror) bölümünü okuyun.

**macOS** (Apple Silicon ve Intel), **Windows** ve **Linux** (x86_64 / Xvfb) ile uyumludur.

```bash
# 1 · Kurulum (uv önerilir; alternatif: pip install gflow-cli)
uv tool install gflow-cli
uv tool run --from gflow-cli playwright install chromium     # tek seferlik, ~150 MB
# isteğe bağlı ekler: `audio narrate` (Edge) için 'gflow-cli[tts]', `video chain`/`assemble`/`captions` için 'gflow-cli[chain]'
# daha sonra: `gflow update` yerinde yükseltir (yeni bir sürüm çıktığında diğer komutlar bir bildirim gösterir)
# Windows pip kurulumunuz 0.99.0 veya daha eski mi? Önce bir kez elle yükseltin (#1298):
#   tüm gflow süreçlerini kapatın, ardından <venv>\Scripts\python.exe -m pip install --upgrade gflow-cli
#   (eklerinizi belirtimde koruyun, örn. "gflow-cli[patchright]", yoksa bağımlılık sürümleri geride kalır)
#   (--user kurulumu için: py -3.X -m pip install --user --upgrade gflow-cli; bkz. https://github.com/ffroliva/gflow-cli/blob/main/docs/USAGE.md#gflow-update)

# 2 · Kimlik doğrulama (tek seferlik, gerçek bir Chrome penceresi açar)
# Bir vekil sunucu (örn. Clash) arkasında mısınız? Önce GFLOW_CLI_BROWSER_PROXY=http://127.0.0.1:7897 ayarlayın; yansılar ve ayrıntılar: docs/USER_GUIDE.md#15-behind-a-proxy-or-a-package-mirror
gflow auth login --browser chrome

# Oturumu kontrol et (kredi harcamaz)
gflow auth status
gflow credits user          # Veo kredi bakiyesi (doğrudan okuma açıkken ve kayıtlı oturum kabul edildiğinde tarayıcı açmaz; kredi harcamaz)
gflow capabilities          # gflow'un burada neleri çalıştırabileceği: yetenek başına yes/no/partial (Flow'a çağrı yapmaz)
gflow models list           # görsel ve video modelleri, takma adlar, süreler (Flow'a çağrı yapmaz)
gflow doctor                # salt-okunur ön kontrol; çıkış kodu 33 çökme değil düzeltilecek bir şey bulduğu anlamına gelir
# Her hata Flow'un sizden ücret kesip kesmediğini ve sonraki adımı söyler: docs/ERRORS.md

# 3 · Üret
gflow image t2i "a hot air balloon over Tokyo at sunrise"
# veya:
gflow video t2v "Slow cinematic push-in on a sunlit forest clearing" --aspect 16:9
# veya yeniden kullanılabilir bir Karakter oluştur (yüz + vücut referansı); bir proje kimliği gerektirir:
# `gflow project create` bir proje oluşturur; `gflow project list` yerel katalogda kayıtlı projeleri gösterir
gflow character create --project <project-id> --name "Aria" --face-prompt "..." --body-prompt "..."
```

Çıktılar `$GFLOW_CLI_OUTPUT_DIR` altına (varsayılan: platformunuzun İndirilenler klasöründe `gflow-cli`, genellikle `~/Downloads/gflow-cli`) kaydedilir veya [`GFLOW_CLI_STORAGE_URI`](docs/EXTERNAL_STORAGE.md) ile S3, MinIO ya da Google Cloud Storage'a yönlendirilebilir. İlk çağrı Chromium ısınırken 30–90 saniye sürer; sonraki çağrılar sıcak oturumu yeniden kullanır.

Tek bir sonucu belirli bir yola kaydetmek için `--out-file PATH` geçin (örneğin `gflow image t2i "..." --out-file hero.png`). Çıktı biçimini (`json|table|id`) seçmek için `-o/--output` kullanın. `-o` için biçim dışı argümanlar, `--out-file` kullanmanızı hatırlatarak çıkış kodu 2 ile hemen hata verir. Bkz. [USAGE](docs/USAGE.md#gflow-image-t2i).

Çıkış kodlarına göre dallanan betikler artık `GFLOW_CLI_EXIT_CODES=2` ayarlayarak kararlı çekirdek kodları (0 başarılı, 1 hata, 2 kullanım, 3 kimlik doğrulama, 130 kesildi) etkinleştirebilir. Bu sonraki bir sürümde varsayılan olacaktır. Bkz. [ERRORS: the stable core](docs/ERRORS.md#exit-codes-the-stable-core-opt-in-in-this-release).

> **Neden `--browser chrome`?** Profili gerçek bir Chrome profili olarak işaretleyen tek stratejidir ve sonraki üretim çalıştırmaları profili bu şekilde açar. Varsayılan `auto`, Chrome kurulu olduğunda bunu otomatik seçer — bkz. [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md).

> **Yerel bir klondan mı kuruyorsunuz?** `uv tool install <path>` komutu **`uv.lock` dosyasını yok sayar** ve bağımlılıkları `pyproject.toml` aralıklarından çözer; bu nedenle size bu projenin hiç test etmediği bir Playwright derlemesi verebilir. Playwright tarayıcı sürücüsünü içerir ve test edilmemiş bir alt sürüm üretimi sessizce kilitleyebilir. Kilitli sürümü açıkça belirtin:
>
> ```bash
> uv tool install --force --with playwright==1.61.0 .
> ```
>
> PyPI'dan kurulum (`uv tool install gflow-cli`) bundan etkilenmez — yayımlanan aralık üst sınırlıdır. Gerçekte hangi sürüme sahip olduğunuzu `uv tool run --from gflow-cli python -c "import importlib.metadata as m; print(m.version('playwright'))"` ile kontrol edin.

Sorun giderme ve çoklu hesap kurulumuyla tam 10 dakikalık rehber için **[USER_GUIDE: Journey 1](docs/USER_GUIDE.md#journey-1--first-time-setup-10-minutes)** belgesine bakın.

## gflow-cli hangi Google Flow modellerini destekler?

`gflow models list` Flow'a çağrı yapmadan kurulu sürümdeki tam listeyi yazdırır (`veo-lite-lp`, daha düşük öncelikli bir Veo 3.1 Lite dahil). `retires on` sütunu Flow'un kullanımdan kaldıracağını duyurduğu modelleri gösterir.

| Tür | Model | Takma Ad | Maks. süre / referans |
|---|---|---|---|
| Video | Omni 1.2 Flash | `omni-1.2` | 15 sn, 7 referans görsel |
| Video | Omni Flash (1.1) | `omni-flash` | 10 sn, 7 referans görsel; Flow 2026-12-05'te kaldırıyor |
| Video | Veo 3.1 Quality | `veo-quality` | 8 sn; Flow 2026-12-05'te kaldırıyor |
| Video | Veo 3.1 Fast | `veo-fast` | 8 sn, 3 referans görsel; Flow 2026-12-05'te kaldırıyor |
| Video | Veo 3.1 Lite | `veo-lite` | 8 sn, 3 referans görsel |
| Görsel | Nano Banana 2.1 | `nano2` | 10 referans görsel |
| Görsel | Nano Banana Pro | `nano-pro` | 10 referans görsel |
| Görsel | Nano Banana 2 Lite | `nano2-lite` | 3 referans görsel |
| Görsel | Imagen 4 (Flow'un kendi menüsünde yok) | `imagen4` | 3 referans görsel |

Flow'un 2026-12-05 tarihinde kullanımdan kaldıracağı üç model yerine `veo-lite` veya `omni-1.2` kullanın ([bilinen sorun](KNOWN_ISSUES.md#flow-retires-omni-11-flash-veo-31-fast-and-veo-31-quality-on-2026-12-05)).

Videolar 16:9 ve 9:16; görseller 16:9, 4:3, 1:1, 3:4 ve 9:16 oranlarında üretilir. Hesabınızın hangi modelleri ve süreleri gördüğü Flow planınıza bağlıdır; `gflow capabilities` bu kurulumun neleri sürebileceğini raporlar.

## gflow-cli Flow kredilerimi ve hesabımı nasıl korur?

- **Yalnızca video kredi harcar.** Görsel üretiminin kredi harcamadığı gözlemlenmiştir (günlük görsel kotasından düşer) ve Sahne birleştirme ücretsizdir.
- **Harcama yapamayan sunucu.** `gflow mcp run --no-spend` kredi harcayan araçları asla kaydetmez; böylece bir ajan bunları hiç çağıramaz.
- **Çok klipli çalıştırmalarda onay öncesi maliyet.** `video chain`, `video extend` ve `movie run` hiçbir şey göndermeden planı göstermek için `--dry-run` alır; `chain` ve `extend` harcama öncesi sorar.
- **Otomatik yeniden denemelerde çift faturalandırma yok.** Ücretli bir istek bir kez gönderilir ve otomatik olarak yeniden denenmez. `gflow run --resume` yerel katalogda henüz bulunmayan satırları yeniden üretir; Flow bir isteği kabul edip sonuç yerel kataloğa kaydedilmeden kesinti olursa `--resume` o satırı tekrar gönderip yeniden faturalandırabilir.
- **Her hata ücret kesilip kesilmediğini söyler.** Hatalar hem CLI'ın `--json` çıktısında hem de MCP yanıtlarında `charged: yes | no | maybe` ve `next_action` (`retry`, `reauth`, `reconcile`) taşır ([ERRORS](docs/ERRORS.md)).
- **İnsan temposunda çalışır.** `gflow` rastgeleleştirilmiş etkileşim zamanlaması ve dengeli gönderimlerle gerçek bir Chrome penceresi kullanır. IP rotasyonu yapmaz, siz ayarlamadıkça vekil sunucu kullanmaz ve varsayılan olarak parmak izi taklidi yapmaz ([ACCOUNT_SAFETY](docs/ACCOUNT_SAFETY.md)).
- **Açıkça hata verir.** Flow arayüzü değiştiğinde tahmin yürütmek yerine belirgin bir çıkış koduyla durur (seçici kayması çıkış kodu 23'tür).

## gflow-cli diğer Google Flow otomasyon araçlarıyla nasıl karşılaştırılır?

Her projenin kendi README dosyasından 2026-11-25 tarihinde karşılaştırılmıştır. "—" o README'de belirtilmediği anlamına gelir.

| | gflow-cli | [GTAI-1/flow-mcp](https://github.com/GTAI-1/flow-mcp) | [ByteLandTechnology/flow-mcp](https://github.com/ByteLandTechnology/flow-mcp) | [kodelyx/flow-agent](https://github.com/kodelyx/flow-agent) | [swissmarley/gflow-cli](https://github.com/swissmarley/gflow-cli) | [crisng95/flowkit](https://github.com/crisng95/flowkit) |
|---|---|---|---|---|---|---|
| Yüzeyler | CLI ve MCP sunucusu (29 araç), eşlik CI'da zorunlu | MCP sunucusu, yerel web paneli | MCP sunucusu | CLI, MCP sunucusu (5 araç), OpenAI uyumlu REST API | CLI | Chrome uzantısı, yerel REST API, panolar |
| Kurulum | `uv tool install gflow-cli` (PyPI) | git clone, npm build | npm, ikili dosyalar | klon, `pip install -e`, paketlenmemiş Chrome uzantısı, köprü süreci | npm | git clone, paketlenmemiş uzantıyı yükleme |
| Resmî MCP Kayıt Defteri | Evet | — | — | — | MCP yok | MCP yok |
| HTTP üzerinden MCP | Evet (`gflow serve`) | — | — | SSE (kendi `docs/MCP_GUIDE.md` belgesine göre) | MCP yok | MCP yok |
| macOS / Windows / Linux | Üçü de | macOS, Windows | Üçü de | — | — | macOS, Linux; Windows'ta WSL veya Git Bash ile |
| Lisans | MIT | — | MIT | MIT (README; depoda lisans dosyası yok) | MIT | MIT |
| Video, görsel, başlangıç/bitiş kareleri, referanslar | Evet | Evet | Evet | Metinden ve görselden videoya, metinden görsele | Evet | Evet |
| 8 sn ötesine uzatma | Evet | Evet | Evet | — | Evet (148 sn'ye kadar) | — |
| Karakterler | Evet | Evet, düzenlemeyle | Evet, düzenlemeyle | — | Evet | Evet |
| Ölçek yükseltme (Upscale) | Video 4K/1080p, görsel 2K/4K | 1080p, 2K | — | — | 2K, 4K | Görsel 2K/4K dışa aktarma; video upscale aktarılmamış |
| OpenAI uyumlu API | Görseller (`gflow serve` üzerinde `POST /v1/images/generations`); video planlandı ([#1365](https://github.com/ffroliva/gflow-cli/issues/1365)) | — | — | Evet | — | — |
| Google hesapları arasında kredi havuzu | Hayır (çağrı başına tek profil; `gflow auth use` ile geçiş) | — | — | Evet | — | — |
| Ajanlar için harcama kilidi (`--no-spend`) | Evet | — | — | — | — | — |
| Her hatada `charged` alanı | Evet | — | — | — | — | — |
| Çağrı başına kredi sınırı | Evet (`--max-credits`, Flow'un kendi teklifinden okunur) | Evet (`max_credits`) | — | — | — | — |
| Videodan videoya düzenleme | Evet (`video edit`, flow.google.com; 5 referansa kadar yerel dosya veya klip) | Evet | Evet | — | — | — |
| Anlatım sesi | Evet (audio narrate: edge-tts veya Gemini sesleri) | Evet | — | — | — | Evet |
| Altyazılar | Evet (`video captions`, `.srt` dışa aktarma) | Evet | — | — | — | — |
| Klipleri birleştirme | Evet, sunucu tarafında (`scene create`) veya müzik ve seslendirmeyle yerelde (`video assemble`) | Evet, müzik ve seslendirmeyle | — | — | Evet (scene) | Evet, müzik ve anlatımla |
| Toplu ajan kipi görsel yığını | Planlandı ([#1361](https://github.com/ffroliva/gflow-cli/issues/1361)) | Evet | — | — | — | — |
| Web kontrol paneli | Planlandı ([#1364](https://github.com/ffroliva/gflow-cli/issues/1364)) | Evet | — | — | — | Evet |
| Makine tarafından okunabilir öz-tanımlama | `capabilities`, `--help --json`, `models`, `llms.txt`, ajan becerileri | — | — | — | — | Ajan becerileri |
| Her sürümde gerçek Flow'a karşı canlı doğrulama | Evet (v0.54.0'dan beri) | — | — | — | — | — |

**Hangisini seçmeliyim?**

- Tek bir PyPI paketinde CLI ve MCP sunucusu, harcama kilidi (`--no-spend`) ve her hatada `charged` alanı için gflow-cli'ı seçin.
- Yerel web paneli için [GTAI-1/flow-mcp](https://github.com/GTAI-1/flow-mcp) projesini seçin.
- Google hesapları arasında kredi havuzu için [kodelyx/flow-agent](https://github.com/kodelyx/flow-agent) projesini seçin.
- Chrome uzantısı ve panoları için [crisng95/flowkit](https://github.com/crisng95/flowkit) projesini seçin.

Planlanan öğelerin tam listesi [#1367](https://github.com/ffroliva/gflow-cli/issues/1367) altındadır. Bir hücredeki düzeltmeler için issue veya pull request açabilirsiniz.

## Sıkça Sorulan Sorular

### gflow-cli resmî bir Google aracı mı?

Hayır. Kendi oturum açmış Google Flow oturumunuzu yöneten bağımsız, MIT lisanslı bir projedir.

### Bu, npm veya JSR'deki gflow-cli paketi mi?

Hayır. npm ve JSR'deki Node.js paketleri dahil başka projeler de bu adı kullanır. Bu gflow-cli, PyPI'daki Python paketidir; [github.com/ffroliva/gflow-cli](https://github.com/ffroliva/gflow-cli) adresinde sürdürülür ve MCP Kayıt Defterinde `io.github.ffroliva/gflow-cli` olarak listelenir.

### Gemini veya Vertex AI API anahtarına ihtiyacım var mı?

Hayır. gflow-cli kullandıkça öde API'sini değil, Google hesabınızın Flow erişimini ve Flow kredilerini kullanır.

### Ücretli mi?

Araç ücretsizdir. Video üretimi Flow kredilerinizi harcar; görsel üretiminin kredi harcamadığı gözlemlenmiştir ancak günlük görsel kotasından düşer.

### Bir yapay zekâ ajanı bana sormadan kredilerimi harcayabilir mi?

`gflow mcp run --no-spend` ile kaydettiğinizde hayır: kredi harcayan araçlar hiç kaydedilmez. Claude Code eklentisi de varsayılan olarak kapalı gelir.

### Başsız (headless) veya bir sunucuda çalışır mı?

Oturum açma bir kez ekran gerektirir. Üretim gerçek bir Chrome penceresini sürdüğünden Linux sunucularında Xvfb altında çalışır. Video için saf HTTP aktarımı Google'ın reCAPTCHA korumasıyla engellenir (bkz. [Mimari](#mimari-ve-mevcut-sınırlamalar)).

### `labs.google` yanında `flow.google.com` ile de çalışır mı?

Evet. Her iki Flow ön yüzünü de destekler ve Flow'un hesabınız için sunduğu sunucuya göre sürücüyü seçer. `flow.google.com` üzerinde yerel dosyalardan metinden videoya, görselden videoya ve referanstan videoya üretimi destekler. Ayrıca Nano Banana görsel üretimini, tek segmentli uzatmayı, görsel ve video ölçek yükseltmeyi ve `@Name` ile karakterleri destekler. Henüz aktarılmamış özellikler labs sürücüsünü kullanmaya devam eder ([Mimari](#mimari-ve-mevcut-sınırlamalar)).

### Hangi Python sürümü gerekir?

Python 3.11 veya üzeri. `uv tool install gflow-cli` bunu sizin yerinize yönetir.

### Bir Google Flow API'si var mı?

Google herkese açık bir Flow API'si yayımlamaz. gflow-cli kendi oturum açmış Flow oturumunuzu yönetir ve bunu bir komut satırı aracı, MCP araçları ve OpenAI uyumlu bir görsel uç noktası (`gflow serve`, `POST /v1/images/generations`) olarak sunar.

### Bir Veo 3 CLI var mı?

Evet. `gflow video t2v`, `i2v` ve `r2v` terminalden Veo 3.1 videosu üretir ve aynı işlemler MCP araçları olarak da sunulur. Omni modelleri de aynı komutları kullanır.

### Google Flow nerelerde kullanılabilir?

Google, desteklenen ülke ve bölgeleri [Where you can use Google Flow](https://support.google.com/flow/answer/16353544) sayfasında listeler. gflow-cli bölgesel erişim sağlamaz. Ağınız bir vekil sunucu veya paket yansısı gerektiriyorsa bkz. [Behind a proxy or a package mirror](https://github.com/ffroliva/gflow-cli/blob/develop/docs/USER_GUIDE.md#15-behind-a-proxy-or-a-package-mirror).


## Örnekler

Tek komut girer, gerçek Flow çıktısı döner. Sol: kütüphanenizde fotogerçekçi bir sahne üreten `gflow image t2i`. Sağ: kareden kareye dönüşüm.

![gflow-cli örnekleri: metinden görsele üretim ve öncesi/sonrası kare dönüşümü](https://raw.githubusercontent.com/ffroliva/gflow-cli/main/docs/assets/examples.webp)

## Demo

![gflow image t2i tek bir 9:16 istemi çalıştırır, structlog çıktısını akıtır ve diske bir PNG yazar](https://raw.githubusercontent.com/ffroliva/gflow-cli/main/docs/assets/example-run.gif)

Oturum açmış bir Flow profiline karşı tek bir `gflow image t2i "..." --aspect 9:16 --model nano2` çağrısı. Terminal, çalıştırmanın `structlog` JSON çıktısını akıtır ve ardından yazılan PNG dosyasını listeler. Chromium arka planda Flow düzenleyicisini sessizce yönetir.

Kaydı [`scripts/record_demo.ps1`](scripts/record_demo.ps1) ile yeniden oluşturabilirsiniz (Windows, OBS, ffmpeg, gifski). Yan yana bölünmüş ekran dahil daha fazla biçim: **[docs/DEMOS.md](docs/DEMOS.md)**.

## Belgeler

[**docs/INDEX.md**](docs/INDEX.md) ana yönlendirme katmanıdır. Hızlı bağlantılar:

**Kullanım verileri:** gflow yerel kullanım sayaçlarını saklar ve etkileşimli bir bildirimden (AB/AEA'da açık onaydan) sonra haftalık toplamları kimlik tanımlayıcısı olmadan yükler. Bkz. [gizlilik bildirimi](docs/TELEMETRY.md) ve [nasıl kapatılır](docs/TELEMETRY.md#turn-usage-uploads-off-or-on).

| Konu | Okuma |
|---|---|
| 🎯 **Başlarken** | [Kullanıcı Kılavuzu](docs/USER_GUIDE.md) · [Kullanım](docs/USAGE.md) · [Yapılandırma](docs/CONFIGURATION.md) |
| **Depolama ve katalog** | [Harici Depolama](docs/EXTERNAL_STORAGE.md) · [Veri Katmanı](docs/DATA_LAYER.md) |
| 🎭 **Karakterler** | [Karakterler](docs/CHARACTER.md), yeniden kullanılabilir özneler (`gflow character`) |
| 🤖 **Ajan ve otomasyon** | [Talimatlar](docs/INSTRUCTIONS.md) (`gflow instructions`, kalıcı brifing kartları) · [Film](docs/MOVIE.md) (`gflow movie`, çok sahneli bildirimler) · [Araçlar](docs/TOOLS.md) (`--tool`, istem yeniden yazma) · [MCP sunucusu](docs/MCP.md) (`gflow mcp run` / `gflow serve`) |
| 🔐 **Kimlik doğrulama ve oturumlar** | [Kimlik Doğrulama](docs/AUTHENTICATION.md) · [Bilinen sorunlar](KNOWN_ISSUES.md) |
| 📣 **Nereden kurulur?** | [Pazar Yerleri](docs/MARKETPLACES.md) (her kanal ve gerçekte ne sunduğu) · [Kapsayıcı](docs/CONTAINER.md) (imajın neden iç gözlem yapabildiği ama üretim yapamadığı) |
| 🏗️ **İç yapı** | [Mimari](docs/ARCHITECTURE.md) · [Güvenlik](docs/SECURITY.md) · [Kullanım verisi bildirimi](docs/TELEMETRY.md) · [Hata Ayıklama](docs/DEBUGGING.md) |
| 📦 **Sürümler** | [Değişiklik Günlüğü](CHANGELOG.md) · [Yol Haritası](ROADMAP.md) · [Sürüm protokolü](RELEASE.md) · [Proje durumu](docs/PROJECT_STATUS.md) |
| 🤝 **Katkıda bulunma** | [Katkıda Bulunma](CONTRIBUTING.md) · [Geliştirme](docs/DEVELOPMENT.md) · [GitHub iş akışı](docs/GITHUB.md) |

## Yapay zekâ ajanları ve LLM'ler için

gflow-cli dört ajan giriş noktası sunar. Aracınızın ilk okuduğunu seçin.

| Dosya | Hedef Kitle | Araçlar |
|---|---|---|
| [**AGENTS.md**](AGENTS.md) | Evrensel kodlama ajanı spesifikasyonu | Cursor · Codex · Aider · Antigravity · Jules · Devin · Windsurf · Zed · Warp · opencode · Copilot |
| [**CLAUDE.md**](CLAUDE.md) | Claude Code'un otomatik yüklenen belleği | Claude Code |
| [**llms.txt**](llms.txt) | LLM tarafından okunabilir özet (`llmstxt.org` biçimi) | Modeli hazırlamak için ChatGPT, Claude veya Gemini'ye yapıştırın |
| [`skills/gflow-cli/SKILL.md`](skills/gflow-cli/SKILL.md) | Claude Code Becerisi | `~/.claude/skills/` içine sembolik bağlantı oluşturun |

Bir ajan tüm komut ağacını CLI'ın kendisinden de okuyabilir: `gflow --help --json`.

Bir MCP istemcisi bağlamak için bkz. [Claude Code, Cursor veya Codex'i Google Flow'a nasıl bağlarım?](#claude-code-cursor-veya-codexi-google-flowa-nasıl-bağlarım)

Herhangi bir ajanı tek satırda hazırlayın. Şunu seçtiğiniz ajana yapıştırın:

> *"Read [AGENTS.md](https://github.com/ffroliva/gflow-cli/blob/main/AGENTS.md) and [docs/INDEX.md](https://github.com/ffroliva/gflow-cli/blob/main/docs/INDEX.md), then help me with my Flow batch."*

## Mimari ve mevcut sınırlamalar

```text
gflow CLI  →  Provider (değiştirilebilir)  →  Flow (ui_automation) / Mock (testler)
                                              ↓
                                      Playwright Chromium (pencereli — varsayılan olarak hem oturum açma hem üretim)
                                              ↓
                              aisandbox-pa.googleapis.com  (Google'ın özel Flow API'si)
```

**Mevcut aktarım:** `ui_automation`, kalıcı bir Playwright Chromium profili üzerinden Flow'u yönetir. Üretim aktarımıdır ve her sürümde uçtan uca doğrulanır (sürüme özgü `LIVE_VERIFICATION_*` kanıt dosyalarına bakın).

**İki Flow ön yüzü:** Google hesapları `labs.google` adresinden `flow.google.com` adresine taşıyor ([#639](https://github.com/ffroliva/gflow-cli/issues/639)). Aynı üründür; farklı bir bileşen araç takımı ve aktarım protokolü (`aisandbox-pa` yerine `batchexecute`) kullanır. Aktarılan sürücü şunları kapsar:

- yerel başlangıç karesinden metinden videoya, görselden videoya ve yerel dosyalardan referanstan videoya;
- yerel dosyalardan metinden görsele ve görselden görsele;
- tek segmentli `video extend`.

Görsel üretimi Nano Banana 2.1 (`nano2`, [#958](https://github.com/ffroliva/gflow-cli/issues/958)), 2 Lite ve Pro modellerini destekler. O sunucuda ölçülen beş en-boy oranını (16:9, 4:3, 1:1, 3:4, 9:16) ve 1–4 adet aralığını destekler. Aktarım sınırında mevcut bir proje gereklidir. Görseller `@Name` ile bir karakteri etiketleyebilir. Referanstan videoya üretim, `--reference-entity` ve `--reference-entity-name` ile adlandırılmış bir karakter alır ([#723](https://github.com/ffroliva/gflow-cli/issues/723)). Yalnızca **agent-only** oluşturucunun sunulduğu bir hesap ([#799](https://github.com/ffroliva/gflow-cli/issues/799)) yalnızca metinden görsele ve metinden videoya erişebilir. Bir görsel `--ref <uuid>`, katalog aynı `--project` içinde kaydettiğinde yerinde referanslanır ([#995](https://github.com/ffroliva/gflow-cli/issues/995)).

Şunlar aktarılana kadar labs sürücüsünü kullanmaya devam eder: diğer UUID medya referansları, adı olmayan varlık kimliği, talimatlar, Imagen 4 ve matrisin geri kalanı (`GFLOW_CLI_FLOW_HOST`, bkz. [CONFIGURATION](docs/CONFIGURATION.md#gflow_cli_flow_host)).

**Engellenen nokta:** video üretimi için saf bir HTTP aktarımı. Video yükleme uç noktası Chrome dışı tarayıcılarda HTTP 401 döndürür. Aktarım ayrıca projenin başsız (headless) bir tarayıcıda oluşturamadığı bir reCAPTCHA belirteci gerektirir. Daha önceki üç HTTP stratejisi (`evaluate_fetch`, `bearer`, `sapisidhash`) araştırma amaçlı olarak üretim yolunun dışında `src/gflow_cli/api/transports/experimental/` altında yer alır.

**Nasıl yardımcı olabilirsiniz:** `aisandbox-pa.googleapis.com` adresini gerçek bir Chrome oturumu dışından yönettiyseniz veya Google'ın buradaki bot karşıtı yapısını anlıyorsanız lütfen bir issue açın. Çalışan bir REST aktarımı sunucusuz dağıtımları ve gerçek yatay eşzamanlılığı mümkün kılacaktır. Ayrıntılar: [docs/ARCHITECTURE.md § Headed-browser dependency](docs/ARCHITECTURE.md#headed-browser-dependency--current-limitation).

## Proje durumu

**Beta.** Bu özellikler `ui_automation` üzerinde uçtan uca çalışır:

- görseller: t2i, i2i, yükleme, ölçek yükseltme ve toplu işlem;
- video: t2v, i2v, r2v, zincirleme, uzatma ve ölçek yükseltme;
- girdilerden kipi seçen `image generate` ve `video generate`;
- altı modelli bir video seçici (Veo 3.1 ve Omni), artı `--duration` ve `--count`.

Tekli üretimlerin ötesinde:

- `gflow movie` çok sahneli bildirimleri işler;
- `gflow instructions` kalıcı Ajan Kipi brifing kartlarını yönetir (kredisiz);
- `gflow character` yeniden kullanılabilir özneleri yönetir;
- `gflow scene` kredisiz sunucu tarafı birleştirme yapar;
- `--tool` istem yeniden yazma araçlarını uygular.

Bir MCP sunucusu (stdio için `gflow mcp run`, Streamable HTTP için `gflow serve`) temel yüzeyi yapay zekâ ajanlarına sunar. CI tarafından zorunlu tutulan bir CLI↔MCP eşlik sözleşmesi iki yüzeyi uyumlu tutar.

Tüm kilometre taşı geçmişi [CHANGELOG.md](CHANGELOG.md) içindedir. Projenin nereye gittiği: [ROADMAP.md](ROADMAP.md).

## gflow-cli'ı destekleyin

`gflow-cli` tek kişi tarafından sürdürülmektedir. Her sürüm gerçek yapay zekâ kredileri harcanarak gerçek Google Flow üzerinde doğrulanır. Sponsorluklar bu doğrulama maliyetini karşılar.

[![Sponsor $5 one-time](https://img.shields.io/badge/☕_sponsor-$5_one--time-ea4aaa?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/ffroliva/sponsorships?frequency=one-time&amount=5)
[![Sponsor $5 a month](https://img.shields.io/badge/💖_sponsor-$5_a_month-ea4aaa?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/ffroliva/sponsorships?frequency=monthly&amount=5)

İşinizde gflow-cli'a mı güveniyorsunuz? Aylık şirket katmanları logonuzu buraya koyar ve Silver ile üstü ayrıca öncelikli issue desteği alır. Tüm katmanlar ve her birinin sundukları: [docs/SPONSORS.md](docs/SPONSORS.md).

Çatalınızda gflow-cli'ı yamaladınız mı? Lütfen bir [issue](https://github.com/ffroliva/gflow-cli/issues) veya pull request açın — yukarı akışa giren bir düzeltme herkese yardımcı olur.

### Onur listesi

<!-- sponsors:start -->
Henüz sponsor yok. <a href="https://github.com/sponsors/ffroliva/sponsorships?frequency=one-time&amp;amount=5">İlk siz olun</a> — her herkese açık sponsor burada listelenir.
<!-- sponsors:end -->

## Lisans ve yasal bilgilendirme

[MIT Lisansı](LICENSE) © 2026 Flavio Oliva (`ffroliva`). MIT lisansı yalnızca `gflow-cli` kodunu kapsar. Flow, Veo model çıktıları veya herhangi bir Google servisi üzerinde hiçbir hak vermez. Üretimleriniz Google'ın kendi koşullarına (Labs Ek Koşulları ve plana özgü abonelik koşulları) tabidir. Bkz. [DISCLAIMER](DISCLAIMER.md).

## Teşekkürler

- [`edge-tts`](https://github.com/rany2/edge-tts), özel bulut API'leri üzerindeki topluluk SDK'ları için tasarım ilhamı.
- [`googleapis/python-genai`](https://github.com/googleapis/python-genai), gelecekteki bir sağlayıcı sürümünün takma ad verebileceği resmî Veo SDK'sı.
- [Keysight, *Google Labs – Flow AI with Veo3: A Network Traffic Analysis*](https://www.keysight.com/blogs/en/tech/nwvs/2025/08/04/google-flow-ai-har-analysis), rota desenlerini doğrulamaya yardımcı olan bağımsız bir yakalama analizi.

---

## İstatistikler

[![GitHub stars](https://img.shields.io/github/stars/ffroliva/gflow-cli?style=social&cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/ffroliva/gflow-cli?style=social&cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli/network/members)
[![GitHub watchers](https://img.shields.io/github/watchers/ffroliva/gflow-cli?style=social&cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli/watchers)
[![GitHub issues](https://img.shields.io/github/issues/ffroliva/gflow-cli?cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli/issues)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/ffroliva/gflow-cli?cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli/pulls)
[![GitHub last commit](https://img.shields.io/github/last-commit/ffroliva/gflow-cli?cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli/commits/main)
[![GitHub repo size](https://img.shields.io/github/repo-size/ffroliva/gflow-cli?cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli)
[![PyPI downloads](https://static.pepy.tech/badge/gflow-cli/month)](https://pepy.tech/project/gflow-cli)

### Proje kalitesi

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=ffroliva_gflow-cli&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ffroliva_gflow-cli)
[![Release](https://github.com/ffroliva/gflow-cli/actions/workflows/release.yml/badge.svg)](https://github.com/ffroliva/gflow-cli/actions/workflows/release.yml)
[![Type checked: pyright](https://img.shields.io/badge/type%20checked-pyright-blue.svg)](https://github.com/microsoft/pyright)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Tests: TDD](https://img.shields.io/badge/tests-TDD-brightgreen.svg)](CONTRIBUTING.md)
[![Platforms: macOS · Windows · Linux](https://img.shields.io/badge/platforms-macOS%20%C2%B7%20Windows%20%C2%B7%20Linux-lightgrey.svg)](docs/CONFIGURATION.md)
[![Ask DeepWiki](https://img.shields.io/badge/Ask-DeepWiki-1f6feb.svg)](https://deepwiki.com/ffroliva/gflow-cli)

### Yıldız geçmişi

<!-- DO NOT strip `sealed_token` from the URLs below. GitHub restricted the public
     /stargazers endpoint (2026-06-30), so star-history can only build this chart from a
     token-authenticated request. Its per-repo cache expires in <3 days, so a tokenless
     URL renders a "GitHub restricted access to star data" placard — served as HTTP 200,
     which is why nothing catches it. `.github/workflows/star-history-watch.yml` probes
     for exactly that. The token is sealed with star-history's key and grants metadata
     read on a public repo; it is safe in a public README, and they recommend it. -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=ffroliva/gflow-cli&type=date&theme=dark&legend=top-left&sealed_token=7kTiE_HExjjY2aT7O-_hMxY_Mf6n-ET17mi_RXcRjCSS5rHSBDMd7xFYCzT_yaXhAXrgF8AGTQc6mny_qfuJc7473KGqb-5U41Dpu-tpZIS1IYl-xVQR9ziGJtL0KWQVyWZU1IoUmLWwo43PhgVTo4MJfmWOvluFJq2zlGxE_iLl9wMRgpwQaiC4ufOK" />
  <img alt="ffroliva/gflow-cli için yıldız geçmişi grafiği" src="https://api.star-history.com/chart?repos=ffroliva/gflow-cli&type=date&legend=top-left&sealed_token=7kTiE_HExjjY2aT7O-_hMxY_Mf6n-ET17mi_RXcRjCSS5rHSBDMd7xFYCzT_yaXhAXrgF8AGTQc6mny_qfuJc7473KGqb-5U41Dpu-tpZIS1IYl-xVQR9ziGJtL0KWQVyWZU1IoUmLWwo43PhgVTo4MJfmWOvluFJq2zlGxE_iLl9wMRgpwQaiC4ufOK" />
</picture>

`gflow-cli` size zaman kazandırıyorsa lütfen depoya ⭐ yıldız verin. Projeyi desteklemenin en ucuz yoludur.
