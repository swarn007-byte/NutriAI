"""Simulate the Render environment: no torch, deploy flags on, temp DB.

Proves two things that would otherwise only surface after a deploy:
  1. requirements-deploy.txt is sufficient — nothing at import time needs torch.
  2. pipeline.warm_models() survives ENABLE_*=false with no weights present.

Uses a temp SQLite file and temp upload dir so the real backend/nutriai.db and
backend/uploads/ are never touched.
"""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="nutriai-deploy-sim-")

# Mirror render.yaml exactly.
os.environ.update(
    DATABASE_URL=f"sqlite:///{TMP}/test.db",
    UPLOAD_DIR=f"{TMP}/uploads",
    MODEL_DIR=f"{TMP}/models",          # empty: the image ships no weights
    JWT_SECRET="test-only-not-a-real-secret",
    ENABLE_TORCH_MODELS="false",
    ENABLE_CALORIECLIP="false",
    ENABLE_SEGMENTATION="false",
    ENABLE_INDIAN_CLASSIFICATION="false",
    SERVE_FRONTEND="false",
    CORS_ORIGINS="",
    CLASSIFIER_TIMEOUT_S="40",
    CLASSIFIER_TTA_PASSES="1",
)

# Make torch and friends unimportable, the way they are in the deploy image.
BLOCKED = {"torch", "torchvision", "ultralytics", "timm"}


class BlockTorch:
    def find_module(self, name, path=None):
        return self if name.split(".")[0] in BLOCKED else None

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"{name} blocked: simulating requirements-deploy.txt")
        return None

    def load_module(self, name):
        raise ImportError(f"{name} blocked: simulating requirements-deploy.txt")


sys.meta_path.insert(0, BlockTorch())

for mod in list(sys.modules):
    if mod.split(".")[0] in BLOCKED:
        del sys.modules[mod]

sys.path.insert(0, "/Users/swarn/Desktop/NutriGuide/backend")

print("--- importing main (no torch available) ---")
import main  # noqa: E402

print("--- booting app via lifespan + calling /api/health ---")
from fastapi.testclient import TestClient  # noqa: E402

with TestClient(main.app) as client:
    r = client.get("/api/health")
    print(f"GET /api/health -> {r.status_code}")
    print(r.json())

print("\n--- torch really was absent? ---")
try:
    import torch  # noqa: F401

    print("!! FAIL: torch was importable")
except ImportError as exc:
    print(f"confirmed: {exc}")
