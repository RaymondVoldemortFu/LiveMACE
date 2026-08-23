"""Persistent private Python module namespaces for loaded extensions."""

from __future__ import annotations

import builtins
import importlib
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec, SourceFileLoader
from importlib.util import find_spec, spec_from_file_location
from pathlib import Path
import sys
from threading import RLock
from types import ModuleType
from typing import Any
from uuid import uuid4


class ExtensionImportError(ImportError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _ExtensionSourceLoader(SourceFileLoader):
    def __init__(
        self,
        fullname: str,
        path: str,
        namespace: "ExtensionModuleNamespace",
    ) -> None:
        super().__init__(fullname, path)
        self._namespace = namespace

    def exec_module(self, module: ModuleType) -> None:
        module.__dict__["__builtins__"] = self._namespace.module_builtins
        super().exec_module(module)


class _ExtensionModuleFinder(MetaPathFinder):
    def __init__(self) -> None:
        self._namespaces: dict[str, ExtensionModuleNamespace] = {}
        self._lock = RLock()

    def register(self, namespace: "ExtensionModuleNamespace") -> None:
        with self._lock:
            self._namespaces[namespace.name] = namespace
            if self not in sys.meta_path:
                sys.meta_path.insert(0, self)

    def unregister(self, namespace: "ExtensionModuleNamespace") -> None:
        with self._lock:
            self._namespaces.pop(namespace.name, None)
            if not self._namespaces:
                try:
                    sys.meta_path.remove(self)
                except ValueError:
                    pass

    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        namespace_name, separator, relative_name = fullname.partition(".")
        if not separator:
            return None
        with self._lock:
            namespace = self._namespaces.get(namespace_name)
        if namespace is None:
            return None
        target_path = namespace.resolve_module(relative_name)
        if target_path is None:
            return None
        source_path, is_package, is_namespace_package = target_path
        if is_namespace_package:
            spec = ModuleSpec(fullname, loader=None, is_package=True)
            spec.submodule_search_locations = [str(source_path)]
            return spec
        loader = _ExtensionSourceLoader(fullname, str(source_path), namespace)
        return spec_from_file_location(
            fullname,
            source_path,
            loader=loader,
            submodule_search_locations=(
                [str(source_path.parent)] if is_package else None
            ),
        )


_FINDER = _ExtensionModuleFinder()


class ExtensionModuleNamespace:
    """One extension root exposed under a unique import namespace."""

    def __init__(self, root: Path, *, module_prefix: str | None = None) -> None:
        self.root = root.resolve(strict=True)
        self.module_prefix = module_prefix
        self.name = f"_alpha_arena_extension_{uuid4().hex}"
        self._installed = False
        module_builtins = dict(vars(builtins))
        module_builtins["__import__"] = self._import
        self.module_builtins = module_builtins

    def install(self) -> None:
        if self._installed:
            return
        package = ModuleType(self.name)
        package.__package__ = self.name
        package.__path__ = [str(self.root)]
        package_spec = ModuleSpec(self.name, loader=None, is_package=True)
        package_spec.submodule_search_locations = [str(self.root)]
        package.__spec__ = package_spec
        _FINDER.register(self)
        sys.modules[self.name] = package
        self._installed = True

    def uninstall(self) -> None:
        _FINDER.unregister(self)
        prefix = f"{self.name}."
        for module_name in tuple(sys.modules):
            if module_name == self.name or module_name.startswith(prefix):
                sys.modules.pop(module_name, None)
        self._installed = False

    def resolve_module(self, relative_name: str) -> tuple[Path, bool, bool] | None:
        relative_name = self._relative_name(relative_name)
        parts = relative_name.split(".")
        if not parts or not all(part.isidentifier() for part in parts):
            return None
        base = self.root.joinpath(*parts)
        package_directory = self._contained_path(base, directory=True)
        if package_directory is not None:
            package_init = self._contained_path(
                package_directory / "__init__.py", directory=False
            )
            if package_init is not None:
                return package_init, True, False
        module_file = self._contained_path(base.with_suffix(".py"), directory=False)
        if module_file is not None:
            return module_file, False, False
        if package_directory is not None:
            return package_directory, True, True
        return None

    def import_module(self, module_name: str) -> ModuleType:
        if not self._installed:
            raise RuntimeError("extension module namespace is not installed")
        if self.resolve_module(module_name) is None:
            try:
                external_spec = find_spec(module_name)
            except (ImportError, AttributeError, ValueError):
                external_spec = None
            code = (
                "ENTRYPOINT_MODULE_OUTSIDE_ROOT"
                if external_spec is not None
                else "ENTRYPOINT_IMPORT_FAILED"
            )
            message = (
                "declared entrypoint module is outside the extension root"
                if external_spec is not None
                else "declared entrypoint module could not be imported"
            )
            raise ExtensionImportError(code, message)
        try:
            relative_name = self._relative_name(module_name)
            return importlib.import_module(f"{self.name}.{relative_name}")
        except ExtensionImportError:
            raise
        except Exception as exc:
            raise ExtensionImportError(
                "ENTRYPOINT_IMPORT_FAILED",
                "declared entrypoint module could not be imported",
            ) from exc

    def _contained_path(self, path: Path, *, directory: bool) -> Path | None:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return None
        if not resolved.is_relative_to(self.root):
            return None
        if directory and not resolved.is_dir():
            return None
        if not directory and not resolved.is_file():
            return None
        return resolved

    def _import(
        self,
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] | list[str] = (),
        level: int = 0,
    ) -> ModuleType:
        if level != 0 or self.resolve_module(name) is None:
            return builtins.__import__(name, globals, locals, fromlist, level)

        relative_name = self._relative_name(name)
        module = importlib.import_module(f"{self.name}.{relative_name}")
        if fromlist:
            for item in fromlist:
                child_name = f"{name}.{item}"
                if item != "*" and self.resolve_module(child_name) is not None:
                    relative_child = self._relative_name(child_name)
                    importlib.import_module(f"{self.name}.{relative_child}")
            return module
        top_level_name = relative_name.split(".", 1)[0]
        return sys.modules[f"{self.name}.{top_level_name}"]

    def _relative_name(self, module_name: str) -> str:
        prefix = self.module_prefix
        if prefix and module_name.startswith(f"{prefix}."):
            return module_name[len(prefix) + 1 :]
        return module_name


__all__ = ["ExtensionImportError", "ExtensionModuleNamespace"]
