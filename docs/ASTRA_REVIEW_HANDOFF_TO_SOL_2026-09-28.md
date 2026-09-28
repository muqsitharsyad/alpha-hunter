# AlphaHunter — Review Astra dan Handoff Implementasi untuk GPT Sol

> **KOREKSI ASTRA, 28 September 2026:** Klaim completion dalam bagian Progress Luna berikut adalah historis dan sebagian tidak didukung verifikasi yang memadai. Authenticated UI smoke mengikuti redirect HTTP ke login; API berhasil bukan bukti UI berhasil. Batch A3 belum membuktikan slow-scan cadence/in-flight shutdown; restore hanya integrity check DB. Rencana koreksi yang berlaku: `ASTRA_HANDOFF_TO_LUNA_ACCEPTANCE_RECOVERY_2026-09-28.md`. Bagian roadmap PRD di bawah tetap relevan. Dokumen koreksi tidak mengubah runtime.

## Progress Luna — Batch A1/A2, 28 September 2026

Implemented and verified in the local source:

- Feasibility result details now persist `slippage_bps`, `feasible`, and `reasons` as an explicit contract.
- Opportunities template reads feasibility JSON with `.get(...)`, so legacy rows missing `slippage_bps` render safely instead of raising HTTP 500.
- Added regression coverage for populated legacy opportunity detail, decision filtering, unknown opportunity ID, and feasibility contract fields.
- Local verification: full pytest suite passed; compileall and `git diff --check` passed.
- Deployed changed runtime files to the server after a timestamped file backup.
- Built server image `alphahunter:1.0.1`, made Compose image selection configurable, and recreated the container with `ALPHAHUNTER_IMAGE=alphahunter:1.0.1`.
- Server container is healthy; authenticated semantic smoke returned 200 for dashboard, opportunities, settings, positions, trades, and learning. No traceback appeared after restart.

Still open:

- Worker Batch A3 completed: scan and position monitoring are isolated, scan failures no longer skip monitoring, cancellation is awaited, and worker task state is cleared on shutdown. Regression tests cover paused monitoring, scan failure isolation, and graceful cancellation.
- Server image `alphahunter:1.0.2` is now running as UID/GID 10001, healthy, restart count 0, memory observed at 105 MiB / 384 MiB and CPU 0.07%.
- Safe paper smoke was executed: bot was started with paper acknowledgment, a provider scan produced 6 pools scanned, 63 wallet events, and 2 evaluated opportunities; bot was then paused again. No live execution was enabled.
- Authenticated server API verification now sees 6 opportunities; latest detail returned 200 with the new feasibility contract keys including `slippage_bps`, `feasible`, and `reasons`.
- Offline restore verification completed in an isolated backup directory: historical DB SHA256 matched `87bfe230...b487f8`, SQLite integrity was `ok`, and expected tables were readable. The live DB was not replaced.
- HTTPS login returned 200 with normal certificate verification through the Tailscale hostname/IP resolution test.

Remaining limits:

- A long-duration 24-hour/7-day soak has not been completed; it cannot be honestly replaced by a short smoke.
- Quantitative Phase 0.5 forward validation and live-money readiness remain intentionally open and are not implied by this deployment.

---

Tanggal review: 28 September 2026, sekitar 21:00 WIB.
Penulis: Astra, sesi Hermes senior-developer.
Status: REQUEST CHANGES — research/paper MVP sudah terbentuk; acceptance produksi belum selesai.
Scope giliran ini: review, pemeriksaan read-only, menjalankan tes lokal, dan dokumentasi. Tidak mengubah kode aplikasi, data produksi, konfigurasi server, atau status bot.

## 1. Jawaban langsung untuk Kin

**Arah besarnya benar, tetapi belum seluruhnya sesuai urutan dan definition of done PRD.**

Yang benar: aplikasi nyata sudah dibuat, bukan sekadar mock; stack diringankan sesuai batas resource; provider dan engine dipisahkan; keselamatan paper-only dipertahankan; sudah ada autentikasi, ledger, alasan penolakan, regression tests, serta deployment privat.

Yang belum tepat: implementasi berbagai modul dan deployment berjalan lebih jauh daripada pembuktian thesis smart-money. PRD §32 meminta Phase 0.5 signal-validation spike SEBELUM full pipeline. Belum ada bukti tahap itu lulus. Nama modul learning/tournament dan banyak tes tidak menggantikan bukti bahwa wallet yang dipilih benar-benar prediktif pada data berikutnya.

Produk saat ini paling jujur disebut **MVP observasi smart-money dengan engine simulasi paper**, bukan autonomous profitable AI fund manager dan belum production-ready sesuai seluruh PRD. Tidak perlu membangun ulang. Prioritas berikutnya memperbaiki integritas kontrak data, pengujian data nyata, reproducibility deployment, kemudian kemampuan memperoleh bukti yang cukup untuk keputusan.

Saya tidak memberikan persentase selesai: belum ada traceability matrix lengkap untuk seluruh acceptance criteria, dan persen berdasarkan jumlah file akan menyesatkan.

## 2. Batas mandat untuk Sol

User meminta catatan lengkap agar Sol menjadi implementer berikutnya. Dokumen ini adalah rencana dan handoff, bukan bukti semua tugas sudah dikerjakan. Saat user memberi izin implementasi, lanjutkan dari repository existing; jangan rewrite stack atau memulai ulang.

Invariants:
- `trading_mode=paper`, `enable_live_execution=False` tetap dipaksa oleh runtime.
- Tidak meminta wallet private key, tidak mengaktifkan real-money execution, tidak memasang paid provider tanpa persetujuan eksplisit.
- UNKNOWN tetap UNKNOWN. Jangan membuat metadata aman palsu, wallet history terverifikasi palsu, atau melemahkan gate supaya demo menghasilkan BUY.
- Tidak reset/drop DB produksi, tidak menghapus volume, tidak memakai `docker compose down -v`.
- Jangan commit/push tanpa diminta. Jangan menampilkan secret atau isi `.env`/operator-login.
- Runtime satu proses dan SQLite; jangan menambahkan worker Uvicorn kedua tanpa redesign locking/state.
- Tetap gunakan UI informatif dan dependensi ringan; jangan menambah Redis/Postgres/Celery/React hanya untuk meniru daftar stack PRD.

## 3. Sumber dan kualitas bukti

### Dibaca ulang pada review ini
- PRD: `C:\Users\kinmin\Downloads\AlphaHunter_AI_PRD_v1.3_Production_Blueprint.docx`.
- Repository: `D:\Coding\Learning\Iseng\AlphaHunter`.
- `README.md`, `docs/QUANT_AUDIT.md`, konfigurasi, Dockerfile.server, Compose.
- `backend/app/{web,config,learning,copy_feasibility,decision_engine,pipeline,main}.py` pada bagian yang relevan.
- `backend/templates/opportunities.html`, `backend/tests/conftest.py`.
- Log container pada rentang kejadian 500 dan metadata DB produksi read-only.
- Rulebook/vault user serta project index; beberapa catatan lama terbukti kedaluwarsa.

### Bukti segar
- `../.venv/Scripts/python.exe -m pytest -o addopts='' -q --tb=short` dari backend: **93 passed in 11.31s**.
- Git branch `master`, belum ada commit. Seluruh berkas proyek masih untracked. `git log -1` exit 128 karena branch belum punya commit, bukan indikasi pytest gagal.
- Container `alphahunter`: `Up 9 hours (healthy)` pada pemeriksaan review. Healthcheck hanya `/login`; label healthy TIDAK membuktikan seluruh UI/worker sehat.
- DB server read-only: bot `paused`; 4 opportunities; 37 wallets; 0 paper positions; 0 paper orders.
- Keempat JSON opportunity feasibility tidak mempunyai key `slippage_bps`.
- Traceback server menunjukkan GET `/app/opportunities` HTTP 500 akibat Jinja Undefined masuk formatter numerik.

### Bukti historis dari sesi deployment, bukan pengukuran ulang lengkap
- HTTPS Tailscale, login operator, dashboard, API dengan autentikasi sempat berhasil.
- Scan live pernah menghasilkan 4 opportunities dan 37 wallets; itu bukan bukti profitable wallet atau trading sukses.
- RAM teramati sekitar 78 MiB saat uji singkat, limit container 384 MiB; bukan load/soak benchmark.
- Bootstrap variables sudah dihapus dari `.env`; backup DB dan Compose dibuat sebelum recreate.
- Sesudah restart, login/dashboard masih berhasil tetapi halaman Opportunities gagal setelah ada data.

### Belum dibuktikan
- Full production UI acceptance dengan data populated dan restart.
- Clean build dengan Dockerfile resmi python slim; deploy menggunakan fallback image.
- Restore DB offline dan rollback release yang benar-benar dijalankan.
- Soak scanner aktif berhari-hari, SLA/SLO PRD, mobile/browser automation penuh.
- Full autonomous qualified signal sampai exit dengan data live.
- Phase 0.5, out-of-sample edge, kandidat ML valid, promosi model yang memenuhi semua PRD gates.

## 4. Kesesuaian dengan plan/PRD

| Area | Penilaian | Tindakan |
|---|---|---|
| Arsitektur ringan | Sesuai arahan user; penyimpangan stack dapat dibenarkan | Pertahankan dan buat ADR scope/resource |
| Paper-only/no custody | Sesuai tujuan v1 | Pertahankan dan tes environment hostile |
| Auth single operator privat | Scope lebih sempit daripada register/RBAC PRD; lebih cocok kebutuhan sekarang | Catat sebagai scope decision, bukan multi-user selesai |
| Discovery dan scanner | Ada pipeline dan scan live | Belum sama dengan qualified smart-money discovery |
| Wallet performance/reputation | Kerangka ada, coverage history belum cukup | Ukur completeness, transfer-aware PnL, forward evidence |
| Token security | Fail-closed baik; evidence gratis masih terbatas | Adapter capability matrix; jangan bypass UNKNOWN |
| Ledger/exits | Perbaikan regression signifikan | Tambah exit realism, crash/restart/concurrency tests |
| UX | Navigasi tersedia; kontrak data populated rusak | Selesaikan 500 dan tes tiap halaman dengan isi |
| Observability | Health provider/halaman ada | Bedakan liveness, readiness, worker heartbeat, degraded data |
| Learning/tournament | Eksploratori/parsial | Jangan klaim statistik dan promotion governance lengkap |
| Phase 0.5 | Belum ada bukti lulus | Jadikan gate penelitian, jangan ditutup dengan synthetic test |
| Release/restore | Deploy parsial, backup ada | Reproducible image, offline restore, release manifest |
| NFR/SLO | Belum tervalidasi | Ukur pada scope single-operator aktual; dokumentasikan deviasi |

Konflik requirement penting: KPI trade pertama <5 menit tidak boleh mengalahkan token safety dan validasi history. Bedakan time-to-first-scan dan time-to-first-qualified-trade. Jika belum ada sinyal layak, tampilkan alasan no-trade; jangan memaksakan entry untuk memenuhi KPI onboarding.

## 5. Temuan prioritas beserta bukti

### P0-01 — HTTP 500: producer/consumer feasibility tidak sepakat

Bukti:
- `backend/app/decision_engine.py:237`: menyimpan `feasibility=getattr(feas, "details", {})`.
- `backend/app/copy_feasibility.py:67-71`: `slippage_bps` ada pada FeasibilityResult, tetapi bukan di `details`.
- `backend/templates/opportunities.html:127`: mengakses `o.feasibility.slippage_bps` dan mengirim ke `money(0)`.
- `backend/app/web.py:55-58`: `_money` hanya menangani None, bukan Undefined.
- Traceback: `TypeError: unsupported format string passed to Undefined.__format__`.
- Template detail line 33 menggunakan pola serupa.

Kesimpulan: bukan cukup bukti masalah restart/SQLite corruption. Kejadian muncul setelah restart, tetapi root cause yang terbukti adalah kontrak JSON dan template; halaman kosong sebelumnya tidak menjalankan row rendering.

Perbaikan yang direncanakan:
1. Buat regression populated-list dan detail menggunakan bentuk data yang benar-benar dipersist decision engine.
2. Definisikan satu kontrak feasibility: schema version, feasible, slippage, reasons, size, depth, age, provenance.
3. Producer simpan field yang consumer perlu; reader mendukung legacy row tanpa key.
4. Unknown ditampilkan `N/A`/`unknown`, bukan 0 bps. Jangan formatter catch-all menutupi data yang salah.
5. Untuk legacy, gunakan CopyEvaluation terkait bila tersedia dan konsisten. Jangan mengambil evaluasi sembarang dari token yang sama. Bila bukti tidak cukup, biarkan unknown.
6. Uji rows kosong, null/missing/partial dict, rejected/qualified, detail, filters, dan restart.

Acceptance: kedua halaman 200 dengan data legacy/baru; slippage tidak difabrikasi; regression merah sebelum patch dan hijau sesudahnya; endpoint produksi yang sama diuji ulang setelah deploy.

### P0-02 — Healthcheck memberi rasa aman yang tidak cukup

`Dockerfile.server:20-21` hanya GET `/login`. Container healthy selama Opportunities 500. Tambahkan semantic smoke authenticated dengan data populated sebagai release gate; bukan membuat Docker liveness bergantung ke vendor eksternal.

Readiness harus mengecek DB/schema/basic service capability. Health worker menyatakan idle/paused/active/degraded secara berbeda; paused bukan failure, bot running dengan heartbeat basi harus terlihat.

Acceptance: UI 500 membuat release gate gagal; provider down menghasilkan degraded/data-stale tanpa restart loop tidak perlu.

### P0-03 — Cadence dan failure scan dapat menunda position monitoring

`backend/app/main.py:439-455`: scan dieksekusi lebih dulu, baru refresh_positions, lalu sleep interval. Bila scan melempar exception, refresh pada cycle itu terlewat; bila scan lambat, monitoring menunggu.

Ini risiko control flow yang terlihat pada source, BELUM failure dengan posisi live teramati. Sekarang DB tidak memiliki posisi.

Rencana: tes scan-raises dan slow-provider; prioritaskan/batasi monitoring dengan budget jelas, pisahkan jadwal scan dan monitor tanpa menambah proses atau overlapping writers. Guard shutdown dan restart worker. `_stop_worker` saat ini cancel tanpa await/reset `_worker_task`; uji dua lifespan berurutan sebelum menentukan patch.

Acceptance: kegagalan scanner tidak menghentikan monitoring existing positions; cadence diukur; paused/emergency tetap monitor; tidak ada duplicate worker setelah restart.

### P1-01 — Data intelligence belum memenuhi tesis produk

Fail-closed pada metadata/history adalah keputusan benar. Tetapi jika semua entry terblokir permanen karena adapter tidak pernah memasok evidence, aplikasi belum memenuhi tujuan autonomous qualified paper trading meski aman.

Rencana:
- Capability matrix per adapter: price/liquidity, swap history, pagination, token security, holder concentration, sellability, timestamps, coverage, quota/cost, unsupported fields.
- Pisahkan wallet discovered, observed, partially covered, qualified. Large trade tidak otomatis smart money.
- Simpan alasan history unverified dan coverage; jangan menaikkan tier dari sample terbaru seolah lifetime PnL lengkap.
- Pilih satu chain dan universe kecil dulu. Investigasi data gratis yang sah, jangan menjanjikan seluruh kontrak safety bisa terpenuhi gratis.
- Jika bukti tidak tersedia, nyatakan research-only feature coverage; paid data merupakan keputusan user, bukan silent fallback.

Acceptance: ada minimal satu jalur qualified synthetic end-to-end yang diberi label test-only, dan laporan jujur coverage live. Synthetic fixture tidak pernah masuk DB produksi atau performance report live.

### P1-02 — Evidence yang disimpan dapat memberi makna keliru

Source review:
- `decision_engine.py:229`: `security_score=80.0` konstan saat menyimpan TokenScore.
- `decision_engine.py:240`: fitur bernama `whale_value_usd` diisi dari `feas.details['our_size']`, bukan nilai whale.

Ini mismatch semantik yang perlu diperbaiki sebelum data digunakan training/report. Jangan mengganti dengan konstanta baru.

Acceptance: security score didukung evidence atau null/unknown; our size dan whale size tersimpan terpisah dengan unit; test expected mapping; historical rows diberi version/invalidity bila tidak dapat dikoreksi dari provenance.

### P1-03 — Governance learning/tournament belum lengkap

Bukti source `backend/app/learning.py`:
- `_stat` annualizes memakai sqrt(252) tanpa membuktikan input adalah daily equally-spaced returns.
- `record_strategy_run` menulis drawdown=0.0, bukan hasil pengukuran.
- Tournament mengecek total sample >=200 dan boolean `oos_verified`; `min_per_regime=30` belum diterapkan di alur yang dibaca.
- BH-FDR helper ada, tetapi run_tournament tidak memanggilnya; tidak terlihat effect-size/common-window/shadow-paper checks lengkap di fungsi tersebut.
- `promote_candidate` menyimpan status promoted tanpa memvalidasi gates; `promote_strategy` mendaftarkan status champion langsung.
- Pencarian backend tidak menemukan penggunaan helper promosi di luar definisi/alias pada pembacaan ini. Jadi ini celah boundary/governance, bukan bukti produksi sedang auto-promote.

Rencana: matikan/larang promotion unsupported di service boundary; pisahkan register candidate dari promote; validation report berbasis dataset/window/config hash, bukan boolean dari caller. Setiap gate wajib diuji. Jangan pasang PyTorch/LLM untuk menutupi kekurangan statistik.

Acceptance: training-only, insufficient per-regime sample, mismatched windows/cost model, failed FDR, negative effect, excessive DD, missing shadow/paper evidence semuanya ditolak; registry candidate tidak mengubah champion; rollback tercatat. Sampai selesai UI tetap menyebut exploratory, bukan validated optimizer.

### P1-04 — Simulasi exit belum cukup realistis

Search source menunjukkan `PaperTrade` dan sell `PaperOrder` dengan `slippage_bps=0.0` pada execution.py:214/227. Catatan QUANT_AUDIT yang menyatakan belum ada full sell-order record sudah tertinggal dari patch sebelumnya: sell records sekarang ada, tetapi realism tetap belum selesai.

Rencana: biaya dan slippage dua arah, model impact/delay/partial/rejected fill yang eksplisit, aturan gap-through-stop, no quote/no sellability tidak disamakan mark=executable. Audit ledger setelah partial exit, fee, manual close, retry dan restart.

Acceptance: trade report memisahkan mark, intended price, simulated execution, realized net PnL dan biaya; no stale fabricated exit; invariant equity/qty/fees/idempotency lulus. Test profitable-looking gross tetapi negative net ditampilkan benar.

### P1-05 — Fallback image tidak pantas menjadi build akhir

`Dockerfile.server` mewarisi `docker-apps-automate-stocks:latest`. Ini workaround karena koneksi registry gagal, bukan arsitektur final. Workdir dipindah ke `/opt/alphahunter` setelah inherited `/app/app/risk/` menutupi `risk.py` dan menyebabkan ImportError.

Risiko: coupling aplikasi lain, tag mutable, dependencies/files/ENV/ports terwaris, build tidak portable. Port 8081 terlihat sebagai image metadata, tidak dipublish oleh Compose. Belum ada bukti secret ikut terwaris; audit sebelum berasumsi aman atau bocor.

Rencana: standalone trusted Python base; bounded registry workaround tanpa mengubah DNS/WARP global server secara serampangan. Lock/pin release artifact, pemeriksaan dependency dan image content, fresh-container test. Pisahkan test dependencies bila berguna. Jangan mengupgrade semua package sekaligus tanpa compatibility/security review.

Acceptance: build dari source tanpa image proyek stocks, runtime non-root, hanya mount/port yang diperlukan, pip compatibility checks, tests dari runtime bersih, release manifest mencatat image digest dan source hash. Fallback tetap didokumentasikan sementara, bukan default diam-diam.

### P1-06 — Konfigurasi dan dokumentasi tidak sinkron

- `config.py:53` masih default PostgreSQL+psycopg, padahal dependency psycopg dihapus dan deployment SQLite.
- README memakai `../data/dev.db`, tetapi data/ di-ignore dan fresh checkout belum tentu mempunyai directory itu.
- README mengklaim Docker belum diuji walaupun fallback sudah dibangun/deploy.
- Instruksi remove bootstrap hanya password bertentangan validator yang mengharuskan email/password hadir berpasangan; server sebelumnya sudah menghapus keduanya.
- `ALPHAHUNTER_FORCE_DEMO` memilih adapter/seed, tetapi UI memakai `demo_mode`; label dapat tidak konsisten. Verifikasi alur env sebelum patch.
- Compose hanya meneruskan sebagian env; semua setting yang dijanjikan configurable harus benar-benar diteruskan atau jelas tidak didukung.
- Vault mencatat 60 tests, belum deploy, demo BUY dan optional Postgres; itu historis, bukan current status.

Acceptance: fresh Windows run dan fresh container run memakai dokumentasi tanpa hidden dirs/default drivers; UI menandai synthetic versus live data dan PAPER dengan jelas; effective config report tidak memuat secret.

### P1-07 — Release hygiene dan test isolation

Seluruh repo untracked, branch master tanpa commit. `git diff --check` pada keadaan ini tidak mengecek semua source untracked; jangan mengklaim hygiene lengkap darinya.

Tests menggunakan satu DB session-wide dan fixture yang menutup posisi dengan UPDATE sebelum test. Ini memberi isolation parsial tetapi bisa menyembunyikan dependensi state/account/worker. Test success bukan jaminan fresh-install/restart path.

Acceptance: test isolation DB/state/clock/adapter/worker dipastikan; test dapat dijalankan sendiri dan bersama; fresh env/container suite; scanner data -> populated UI test; secret/artifact inventory sebelum user memutuskan commit. Jangan otomatis commit.

### P1-08 — Backup bukan restore proof

Backup tercatat di sesi sebelumnya, belum ada bukti restore boot offline. Harus uji restore ke DB/volume TERPISAH, dengan worker dan outbound network dinonaktifkan, bukan restore menimpa produksi.

Acceptance: SQLite backup API, integrity/foreign-key checks, row/entity comparison, restore app boot, login recovery, documented restart/rollback yang menjaga secret/account; jadwal dan retention backup jelas. Backup berisi auth hash/email, perlakukan sebagai data sensitif.

## 6. Tambahan yang bernilai, setelah blocker dasar

Urutan manfaat, bukan wishlist tanpa batas:

1. **Panel Why no trades?** Waterfall per scan: pools ditemukan -> whale events -> history qualified -> safety known -> feasible -> risk pass -> order. Bedakan zero signal, feed error, rate-limited, unknown safety, bot paused. Sertakan last successful scan dan next expected cycle.
2. **Evidence/provenance drawer** per decision: provider, observed_at/source_ts, TTL, history coverage, security evidence, model/config version, input/output hash. Evidence ringkas lebih penting daripada chat AI.
3. **Decision replay offline** menggunakan snapshot tersimpan, tanpa memanggil data masa kini. Hasil pada versi sama reproducible; jangan mendaftarkan phantom live orders.
4. **Operator onboarding**: pilih virtual capital sebelum order pertama, risk profile, acknowledge paper disclaimer; cegah reset capital setelah history. UI Start sekarang tidak perlu dianggap acknowledgement eksplisit hanya karena backend menulis flag true.
5. **Mode labeling**: Synthetic demo, Live-data observation, Paper execution. Semua tetap simulated funds; tidak ada toggle live-money.
6. **Health/incident history**: heartbeat, scan duration, backlog, provider degraded/recovered, stale positions, DB/WAL size, error correlation. Telegram state-change only, bukan spam tiap poll.
7. **Performance report net costs**: trade count yang benar, per-position aggregate versus partial-exit legs, realized/unrealized, fees, drawdown, independent sample caveat. Export CSV/JSON yang sanitized.
8. **Retention/pagination**: query bounded, indexes berdasarkan query nyata, purge policy yang menjaga audit/provenance dan menghormati kebutuhan PRD; tidak langsung menghapus data lama.
9. **Homer shortcut** setelah healthy end-to-end, tanpa mengubah shared dashboard sebelum scope disetujui.

Ditunda: multi-chain besar, news/sentiment berbayar, chatbot sebagai decider, auto-training terus menerus, vector DB, banyak microservices, leverage, private keys, live trading. Semua itu menambah kompleksitas sebelum core product terbukti.

## 7. Roadmap implementasi bertahap dan gate

### Batch A — Bisa dipakai dengan data populated
- A1 [S/M]: Regression dan fix feasibility contract + legacy renderer (P0-01). Files: decision_engine, opportunities template, test UI; batasi scope.
- A2 [S/M]: Semantic smoke release semua screen list/detail/data states (P0-02). Jangan cuma HTTP 200, cek domain content.
- A3 [M]: Worker failure/monitor cadence/shutdown tests dan minimum fix (P0-03).
Checkpoint A: 93 existing tests + regression baru lulus; produksi Opportunities/detail 200 sesudah restart, bot mode dan no-trade reason benar. Tidak claim edge.

### Batch B — Rilis reproducible dan recovery
- B1 [M]: Clean image, dependency/config coherence (P1-05/06).
- B2 [M]: Fresh-install + runtime integration/test isolation (P1-07).
- B3 [M]: Backup/restore offline + release/rollback runbook (P1-08).
Checkpoint B: standalone image terverifikasi, manifest/hash/backup ada, recovery diuji di environment terpisah, neighbor services tidak terdampak. Docs/vault diperbarui.

### Batch C — Integritas data dan simulasi
- C1 [M]: Capability matrix + source quality/provenance.
- C2 [M]: Semantic evidence corrections (security score dan whale/own size) beserta versioning historical data.
- C3 [M, pecah entry/exit/retry bila melebar]: Execution realism dan ledger invariant tests.
- C4 [M]: Why-no-trades panel dengan data per scan.
Checkpoint C: trace satu decision dari raw evidence sampai rejection/fill dan net ledger; unknown tidak pernah menjadi pass, zero, atau synthetic live evidence.

### Batch D — Kembali ke Phase 0.5 dan validasi forward
- D1: Tulis research protocol sebelum memilih winner: universe/cutoff/time split/cost model/baseline/outcome horizon/independent samples.
- D2: Kumpulkan historical point-in-time jika tersedia; jika tidak, katakan historical thesis belum dapat diuji dan gunakan forward collection, jangan merekonstruksi masa lalu dari ranking wallet saat ini.
- D3: Jalankan deterministic replay dan chronological holdout; laporkan coverage/dropouts/delisted/unsellable outcomes, bukan hanya survivors.
- D4: Laporan go/no-go dengan uncertainty dan hasil negatif. Target PRD bukan hasil yang boleh direkayasa. Baseline dan definisi hit-rate wajib dibekukan dulu.
Checkpoint D: reproducible evidence package. Bila hasil tidak mendukung thesis, tetap research mode/pivot dengan keputusan user; jangan menambah modal atau melonggarkan risk untuk mengejar trades.

### Batch E — Learning yang dapat dipercaya, opsional setelah D
- E1: Candidate registry != promotion; enforce gates pada boundary.
- E2: Tournament common windows/costs/per-regime/min samples/FDR/effect/DD dengan artifact provenance.
- E3: Shadow -> paper -> compare champion -> auditable promote/rollback.
Checkpoint E: learning acceptance §19/20/34 benar-benar dipenuhi, bukan hanya tabel/model bernama sama.

Ketergantungan: A -> B; C1/C2 dapat direview paralel dengan B tetapi deploy terkoordinasi; C -> D; E menunggu evidence D. Riset data feasibility bisa dimulai sejak A secara read-only. Jangan menunggu seluruh enhancement selesai untuk menyerahkan Batch A yang usable.

## 8. Rencana verification Sol

Perubahan kecil: RED regression -> patch minimal -> GREEN -> full suite -> review diff/source -> container smoke -> live exact-route check -> record.

Test matrix minimum:
- Blank DB, populated DB, legacy JSON, optional fields missing, None, finite/non-finite numeric inputs.
- Page lists + detail + filters + unknown ID, unauthorized redirects/API rejection, HTML escaping data provider.
- Auth HTTPS cookies/CSRF/logout semantics/rate limit, bootstrap removed + restart.
- API dan UI controls berbagi invariants, bukan implementasi terpisah yang drift.
- Price stale/future/missing, provider timeout/429/malformed payload, scan error tidak mematikan monitoring.
- Entry/partial exit/TP/SL/trailing/manual close; repeated requests/idempotency; crash/restart; funds/qty/concentration consistency.
- Same event replay tidak menambah duplicate order atau sample palsu; wallet coverage test.
- Promotion refused dengan missing/failing evidence; registering strategy tidak otomatis mengganti active champion.
- Restore isolated + health + domain-state comparison.

Live validation stages yang diusulkan (belum dijalankan): short supervised smoke; 24h operation; kemudian 7-day stability observation dengan laporan resource/error/lag, sambil research sample berkembang. Durasi ini merupakan gate operasional, bukan bukti profit atau pengganti horizon KPI 90 hari PRD. Jangan menganggap 9 jam container healthy dalam paused mode sebagai 9 jam scanner soak.

Laporan akhir tiap batch wajib memisahkan: verified command/result, skipped/unverified, assumptions, remaining blockers, access/recovery steps. Jika upstream tak bisa diakses, report blocker; jangan substitusi output realistis palsu.

## 9. Handoff runtime dan akses non-secret

- Local root: `D:\Coding\Learning\Iseng\AlphaHunter`.
- Python: `.venv/Scripts/python.exe`; tests dijalankan dari backend.
- PRD path di §3; baca lagi sebelum mengklaim compliance penuh.
- Server: `kinmin@100.117.117.103`, SSH key `C:/Users/kinmin/.ssh/homeserver_kinmin_ed25519`.
- Server root: `/var/docker-apps/alphahunter`.
- Container: `alphahunter`; image tag `alphahunter:1.0.0` (tag sama pernah direbuild; jangan pakai tag sebagai immutable release identifier).
- Workdir fallback runtime: `/opt/alphahunter`.
- DB: `/data/alphahunter.db`; named volume `alphahunter_alphahunter-data`.
- Bind: `127.0.0.1:8010`; HTTPS origin: `https://aiops-homeserver-1.tail0b6355.ts.net:8444` (tailnet only).
- Tailscale services existing di 443, 8443, 9223 harus tetap utuh. Jangan `tailscale serve reset`.
- Credential file existing: `/var/docker-apps/alphahunter/operator-login.txt` mode 600. Lokasi saja boleh disebut; nilai tidak boleh dibaca ke output/chat/notes. Gunakan authorized secret-safe flow bila autentikasi dibutuhkan.
- Bot pada review ini paused. Jangan Start tanpa kebutuhan smoke yang jelas dan authorization implementasi.
- Current source belum punya commit; simpan file-level snapshot/hash sebelum perubahan.

Backups historis yang perlu dicek keberadaan/hash lagi sebelum dipakai:
- `/var/docker-apps/alphahunter/backups/pre-restart-20260928-113502.db`
  SHA256 `87bfe230f156f8e30f0552bf01a69a6dbca15db502094f0242681febc6b487f8`.
- `/var/docker-apps/alphahunter/docker-compose.yml.before-20260928-113502.bak`
  MD5 `ca41d9643aa48129f8c2cd7158e3ff30`.
- `/var/docker-apps/.alphahunter-serve-before-20260928-113014.json`
  MD5 `ccc9082b806db865dccf18d268ab1620`.
- `.env.post-bootstrap.20260928-043504.bak` memuat secret konfigurasi; jangan tampilkan atau lampirkan.

Narrow containment bila deployment bermasalah (bukan sudah dieksekusi pada review): pause via authorized application control; `docker compose stop alphahunter` dari root server; jika perlu nonaktifkan HANYA proxy 8444: `sudo tailscale serve --https=8444 off`. Ini menghentikan akses/runtime, bukan rollback data. Jangan restore DB in-place tanpa prosedur dan explicit scope.

Penting: `tailscale serve status --json` adalah snapshot diagnostik; jangan asumsikan identik format `set-config`. Tool sebelumnya `get-config --all` tidak menghasilkan file yang diharapkan. Verifikasi CLI actual sebelum restore konfigurasi.

Known execution pitfalls:
- Terminal Windows menggunakan Git Bash/MSYS; native Python/git memakai path `C:/...`/`D:/...`, bukan mengasumsikan `/d/...` otomatis diterjemahkan.
- `/dev/null` pernah menyebabkan curl exit 23 di Windows walaupun response 200; gunakan subprocess capture atau Python HTTP client.
- Server pernah gagal resolve hostname Tailscale sendiri; client laptop bisa resolve. Jangan disable TLS verify. Resolve target IP sambil mempertahankan hostname/SNI bila perlu.
- Official registry download pernah IPv6 reset/DNS failure. Jangan mengubah jaringan shared host global tanpa scope.
- `docs/QUANT_AUDIT.md` dan README mengandung status historis; jangan menyamakan dengan latest state.

## 10. Definition of done yang harus dibedakan

### DOD-A: usable private paper MVP
Login -> configure -> Start -> scan -> explain -> inspect opportunities/detail -> observe states -> Pause; all pages populated aman; restart persistence; price guards; clear mode/data labels; release+restore tested; monitoring meaningful; no undisclosed known P0. Qualified test-only fixture membuktikan lifecycle, tanpa mengklaim live edge.

### DOD-B: credible research platform
Data provenance/coverage, replay, point-in-time study, net costs, bias controls, rejected outcomes, uncertainty/go-no-go, no misleading promotion claims. Hasil negatif tetap deliverable valid.

### DOD-C: full PRD learning acceptance
Statistical and staged promotion/rollback governance benar-benar diuji; seluruh FR/AC/NFR traceability dibuktikan atau deviasi disetujui user.

Tidak ada DOD yang mengizinkan live-money secara implisit. Live execution adalah proyek terpisah, perlu keputusan user dan batas keamanan baru.

## 11. Prompt siap pakai untuk GPT Sol

> Lanjutkan AlphaHunter existing, jangan mulai ulang. Baca rulebook Second Brain, PRD v1.3, dan `docs/ASTRA_REVIEW_HANDOFF_TO_SOL_2026-09-28.md`. Verifikasi live/source state ulang karena dokumen adalah snapshot. Kerjakan Batch A terlebih dahulu: regression kontrak feasibility dan HTTP 500 populated Opportunities, semantic UI smoke, worker monitoring reliability. Jangan tambahkan fitur AI/ML besar atau live execution. Pertahankan UNKNOWN fail-closed dan paper-only. Setelah Batch A selesai, laporkan bukti tes, exact-route deployment verification, backup/rollback dan blocker; lanjut Batch B/C hanya sesuai mandat user. Jangan commit/push/reset DB. Credential tidak boleh tampil. Bukti profit dan Phase 0.5 masih belum ada; jangan mengklaim semua PRD selesai berdasarkan unit tests.

## 12. Kesimpulan reviewer

Kualitas keputusan stack dan pagar keselamatan sudah berada di arah yang benar. Kekurangan utama adalah keterhubungan antar lapisan, realism/evidence, serta disiplin acceptance/release, bukan kurangnya teknologi. Langkah paling produktif sekarang: **selesaikan jalur kecil yang benar-benar bisa dipakai, buat hasilnya dapat diaudit dan direproduksi, baru kembangkan learning berdasarkan bukti.**
