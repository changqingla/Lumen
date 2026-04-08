"""Regression tests for provisioner kubeconfig path handling."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _load_provisioner_module():
    """Load infra/provisioners/app.py as an importable test module."""
    module_path = next(
        (
            parent / "infra" / "provisioners" / "app.py"
            for parent in Path(__file__).resolve().parents
            if (parent / "infra" / "provisioners" / "app.py").exists()
        ),
        None,
    )
    assert module_path is not None, "Could not locate infra/provisioners/app.py"

    # Provisioner 代码在测试环境中只需要最小依赖面；
    # 若未安装 kubernetes，则注入轻量 stub，避免导入阶段失败。
    if "kubernetes" not in sys.modules:
        kubernetes_module = types.ModuleType("kubernetes")
        kubernetes_client_module = types.ModuleType("kubernetes.client")
        kubernetes_config_module = types.ModuleType("kubernetes.config")
        kubernetes_rest_module = types.ModuleType("kubernetes.client.rest")

        class _FakeCoreV1Api:
            def __init__(self, *args, **kwargs):
                pass

        class _FakeApiClient:
            def __init__(self, *args, **kwargs):
                pass

        class _FakeConfiguration:
            host = ""
            verify_ssl = True

            @classmethod
            def get_default_copy(cls):
                return cls()

        class _FakeApiException(Exception):
            def __init__(self, status=None):
                super().__init__(status)
                self.status = status

        kubernetes_client_module.CoreV1Api = _FakeCoreV1Api
        kubernetes_client_module.ApiClient = _FakeApiClient
        kubernetes_client_module.Configuration = _FakeConfiguration
        kubernetes_rest_module.ApiException = _FakeApiException
        kubernetes_config_module.load_kube_config = lambda *args, **kwargs: None
        kubernetes_config_module.load_incluster_config = lambda *args, **kwargs: None

        kubernetes_module.client = kubernetes_client_module
        kubernetes_module.config = kubernetes_config_module

        sys.modules["kubernetes"] = kubernetes_module
        sys.modules["kubernetes.client"] = kubernetes_client_module
        sys.modules["kubernetes.config"] = kubernetes_config_module
        sys.modules["kubernetes.client.rest"] = kubernetes_rest_module

    spec = importlib.util.spec_from_file_location("provisioner_app_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wait_for_kubeconfig_rejects_directory(tmp_path):
    """Directory mount at kubeconfig path should fail fast with clear error."""
    provisioner_module = _load_provisioner_module()
    kubeconfig_dir = tmp_path / "config_dir"
    kubeconfig_dir.mkdir()

    provisioner_module.KUBECONFIG_PATH = str(kubeconfig_dir)

    try:
        provisioner_module._wait_for_kubeconfig(timeout=1)
        raise AssertionError("Expected RuntimeError for directory kubeconfig path")
    except RuntimeError as exc:
        assert "directory" in str(exc)


def test_wait_for_kubeconfig_accepts_file(tmp_path):
    """Regular file mount should pass readiness wait."""
    provisioner_module = _load_provisioner_module()
    kubeconfig_file = tmp_path / "config"
    kubeconfig_file.write_text("apiVersion: v1\n")

    provisioner_module.KUBECONFIG_PATH = str(kubeconfig_file)

    # Should return immediately without raising.
    provisioner_module._wait_for_kubeconfig(timeout=1)


def test_init_k8s_client_rejects_directory_path(tmp_path):
    """KUBECONFIG_PATH that resolves to a directory should be rejected."""
    provisioner_module = _load_provisioner_module()
    kubeconfig_dir = tmp_path / "config_dir"
    kubeconfig_dir.mkdir()

    provisioner_module.KUBECONFIG_PATH = str(kubeconfig_dir)

    try:
        provisioner_module._init_k8s_client()
        raise AssertionError("Expected RuntimeError for directory kubeconfig path")
    except RuntimeError as exc:
        assert "expected a file" in str(exc)


def test_init_k8s_client_uses_file_kubeconfig(tmp_path, monkeypatch):
    """When file exists, provisioner should load kubeconfig file path."""
    provisioner_module = _load_provisioner_module()
    kubeconfig_file = tmp_path / "config"
    kubeconfig_file.write_text("apiVersion: v1\n")

    called: dict[str, object] = {}

    def fake_load_kube_config(config_file: str):
        called["config_file"] = config_file

    monkeypatch.setattr(
        provisioner_module.k8s_config,
        "load_kube_config",
        fake_load_kube_config,
    )
    monkeypatch.setattr(
        provisioner_module.k8s_client,
        "CoreV1Api",
        lambda *args, **kwargs: "core-v1",
    )

    provisioner_module.KUBECONFIG_PATH = str(kubeconfig_file)

    result = provisioner_module._init_k8s_client()

    assert called["config_file"] == str(kubeconfig_file)
    assert result == "core-v1"


def test_init_k8s_client_falls_back_to_incluster_when_missing(tmp_path, monkeypatch):
    """When kubeconfig file is missing, in-cluster config should be attempted."""
    provisioner_module = _load_provisioner_module()
    missing_path = tmp_path / "missing-config"

    calls: dict[str, int] = {"incluster": 0}

    def fake_load_incluster_config():
        calls["incluster"] += 1

    monkeypatch.setattr(
        provisioner_module.k8s_config,
        "load_incluster_config",
        fake_load_incluster_config,
    )
    monkeypatch.setattr(
        provisioner_module.k8s_client,
        "CoreV1Api",
        lambda *args, **kwargs: "core-v1",
    )

    provisioner_module.KUBECONFIG_PATH = str(missing_path)

    result = provisioner_module._init_k8s_client()

    assert calls["incluster"] == 1
    assert result == "core-v1"
