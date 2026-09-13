# Deploying Nutri-AI as an installable APK

Until now the only way to run Nutri-AI on a phone was Expo Go pointed at a
laptop on the same WiFi. This document covers shipping a `.apk` anyone can
install and use from any network.

**The APK is the easy part.** An APK is only as reachable as its backend, so
most of this is about getting the backend off `localhost`.

## Architecture

Three units. The split is not new — `config.py` has had `classifier_url` and
`model_api/` has had its own Dockerfile since early on.

```
  APK (EAS Build)  ──HTTPS──>  Render free web service  ──HTTPS──>  HF Space
  EXPO_PUBLIC_API_URL          nutriai-api (no torch)              classifier
                                      │                            (41 MB)
                                      └──> Neon Postgres
```

Why the classifier is separate: it is the only stage needing torch. Keeping it
out means the API image is ~250 MB instead of ~3 GB and fits Render's free
512 MB box. The backend treats it as optional — if the Space is asleep or
`CLASSIFIER_URL` is unset, every meal still gets an answer, just a coarser one.

`backend/models/` is 706 MB locally, but **nothing gets deployed with weights
except the Space, which needs one 41 MB file.**

---

## Step 1 — Classifier on a Hugging Face Space

### 1a. Upload the checkpoint to a model repo

Weights go in a *model repo*, not in the Space's git. `model_api/app.py:97-105`
downloads them at boot via `HF_REPO_ID`.

> `efficientnet_v1.pt` and `efficientnet_kaggle.pt` are byte-identical (verified
> by sha256) — one file, two names. Upload `efficientnet_v1.pt`.

1. huggingface.co → New Model → name it e.g. `nutriai-classifier`. Private is fine.
2. Upload `backend/models/efficientnet_v1.pt` (41 MB) via the web UI, or:
   ```bash
   pip install -U huggingface_hub
   hf auth login
   hf upload <your-username>/nutriai-classifier \
     backend/models/efficientnet_v1.pt efficientnet_v1.pt
   ```
   > On `huggingface_hub` older than 0.34 the binary is `huggingface-cli`
   > instead of `hf` (`huggingface-cli login` / `huggingface-cli upload`, same
   > arguments). Verified against 1.20.1, where `hf` is correct.

### 1b. Create the Space

1. huggingface.co → New Space → **SDK: Docker**, blank template. Free CPU tier.
2. Push `model_api/`'s contents to the Space repo. Its `README.md` frontmatter
   already declares `sdk: docker` and `app_port: 7860` — keep it as the Space's
   README or the port will not be picked up.
   ```bash
   git clone https://huggingface.co/spaces/<user>/<space> /tmp/nutriai-space
   cp model_api/{Dockerfile,app.py,requirements.txt,README.md} /tmp/nutriai-space/
   cd /tmp/nutriai-space && git add -A && git commit -m "Nutri-AI classifier" && git push
   ```
3. Space → Settings → **Variables and secrets**:

   | Name | Value |
   | --- | --- |
   | `HF_REPO_ID` | `<your-username>/nutriai-classifier` |
   | `HF_FILENAME` | `efficientnet_v1.pt` |
   | `HF_TOKEN` | a read token (only if the model repo is private) |
   | `API_TOKEN` | invent a long random string — **save it**, Render needs it |
   | `TORCH_THREADS` | `2` |

   `API_TOKEN` is not optional in spirit: `app.py:49` enforces it, and without
   it you are running open inference on free compute.

### 1c. Verify before moving on

```bash
curl -sS https://<user>-<space>.hf.space/health
# expect status "ok"; "degraded" means the checkpoint did not download —
# check HF_REPO_ID/HF_FILENAME and the Space build logs.

curl -sS -X POST https://<user>-<space>.hf.space/classify \
  -H "Authorization: Bearer <API_TOKEN>" \
  -F images=@some-food-photo.jpg
# expect JSON: engine "efficientnet_b3" + a label and confidence
```

First call after a sleep takes 30-60 s. That is expected, not a failure.

---

## Step 2 — Postgres

Render's free plan has **no persistent disk**, so the default SQLite file would
be wiped on every redeploy, taking every account with it. A managed Postgres is
required, not optional.

1. neon.tech (or supabase.com) → new project → copy the connection string.
2. Convert the scheme to the driver this project uses — `psycopg[binary]` v3:
   ```
   postgres://user:pass@host/db          ← what Neon gives you
   postgresql+psycopg://user:pass@host/db ← what DATABASE_URL needs
   ```
   Getting this wrong produces a driver error at boot, not a clear message.
3. Keep `?sslmode=require` if present.

> **No migrations.** `db.py:176` uses `Base.metadata.create_all`, so tables are
> created on first boot, but later schema changes need a manual `ALTER` or a
> wipe. Worth knowing before real users have data in there.

---

## Step 3 — Backend on Render

`render.yaml` is committed, so this is a blueprint launch rather than dashboard
clicking.

1. Push this branch to GitHub.
2. render.com → **New → Blueprint** → pick the repo. It reads `render.yaml`.
3. Render prompts for the four `sync: false` values:

   | Variable | Value |
   | --- | --- |
   | `DATABASE_URL` | the `postgresql+psycopg://...` string from step 2 |
   | `CLASSIFIER_URL` | `https://<user>-<space>.hf.space/classify` |
   | `CLASSIFIER_TOKEN` | the `API_TOKEN` from step 1b |
   | `USDA_API_KEY` | your key, or leave blank to use the bundled IFCT tables |

   `JWT_SECRET` is generated by Render automatically (`generateValue: true`).
   **Do not set it by hand to the value in `.env.example`** — that string is
   public in this repo, and anyone with it can mint a valid token for any account.

4. Deploy. First build takes ~5 min. Then:
   ```bash
   curl -sS https://nutriai-api.onrender.com/api/health
   ```
   Expect `200` with `"engine": "heuristic"` and `"database": "postgresql"`.
   The classifier shows `trained_model: false` — correct, it is remote now.

5. If the service goes live but Render reports failure: check the port. The
   Dockerfile reads `$PORT` at runtime for exactly this reason.

---

## Step 4 — Build the APK

```bash
npm i -g eas-cli
cd mobile
eas login          # needs a free Expo account
eas init           # writes extra.eas.projectId into app.json
```

Then set the real backend URL in `mobile/eas.json` — both the `preview` and
`production` profiles have a placeholder `https://nutriai-api.onrender.com`.
Baking it into the profile (rather than reading `.env`) is what makes the
binary work on a stranger's phone.

```bash
eas build -p android --profile preview
```

EAS builds in the cloud (~10-15 min) and returns a download URL. `preview` is
the profile that matters: `android.buildType: "apk"`. The EAS default for
Android is `.aab`, which is a Play Store upload format and **cannot be
sideloaded** — if you get a `.aab`, you used the wrong profile.

To install: open the URL on the phone, allow "install from unknown sources".

---

## Step 5 — Verify it actually works

The only test that proves anything:

1. Install the APK.
2. **Turn WiFi off. Use mobile data.** This is what proves the LAN dependency
   is gone — on WiFi it might still be reaching your laptop.
3. Register a fresh account, capture a meal, confirm nutrition comes back.
4. Force-quit and reopen — you should still be logged in
   (`App.js:222-240`, SecureStore).
5. Check history renders the meal.

### Known rough edges

- **Cold starts stack.** Render free sleeps after 15 min idle; the Space sleeps
  after ~48 h. A first scan after a quiet night waits for both. `App.js:187-219`
  retries 3× with backoff and `CLASSIFIER_TIMEOUT_S=40` absorbs it, so it
  should succeed — but it will feel slow.
  **Demoing? Hit `/api/health` a few minutes beforehand to wake it.**
- **Meal photos are ephemeral.** No persistent disk on Render free, so images
  vanish on redeploy. `_media_urls_for` (`main.py:115-128`) returns `None` for
  a missing file, so old meals keep their nutrition numbers and just lose the
  thumbnail. Fix when it matters: object storage behind `_image_paths`.
- **Hosted accuracy is lower than local.** CalorieCLIP and segmentation are off
  (their weights are not in the image), so the hosted pipeline runs the remote
  EfficientNet classifier plus heuristics. Worth measuring rather than assuming.
- **iOS is not covered.** `app.json` has no
  `NSLocationWhenInUseUsageDescription`, and `App.js:269` requests location —
  that combination crashes on iOS. Android declares `ACCESS_COARSE_LOCATION`
  and is fine. Add the iOS string before ever building for iOS.

---

## Re-verifying the torch-free assumption

The whole design rests on "the backend runs without torch." To re-check after
changing dependencies or touching `pipeline.warm_models()`:

```bash
cd backend && python3 tools/verify_deploy_env.py
```

It blocks `torch`/`torchvision`/`ultralytics`/`timm` at import, applies the
`render.yaml` env, and boots the app against a temp DB. Expect
`GET /api/health -> 200`. Anything else means the deploy image would fail too.
