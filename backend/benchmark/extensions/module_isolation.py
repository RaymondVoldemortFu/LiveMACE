"""Persistent private Python module namespaces for loaded extensions."""

from __future__ import annotations

import builtins
from collections.abc import Callable
from functools import wraps
import importlib
from importlib import resources as _stdlib_resources
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec, SourceFileLoader
from importlib import util as _stdlib_util
from importlib.util import find_spec, spec_from_file_location
from inspect import signature
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


def _accepts_no_arguments(function: Callable[..., Any]) -> bool:
    try:
        signature(function).bind()
    except TypeError:
        return False
    return True


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
        importlib_proxy = ModuleType("importlib")
        importlib_proxy.__dict__.update(vars(importlib))
        importlib_proxy.import_module = self._dynamic_import_module
        self._importlib_util_proxy = ModuleType("importlib.util")
        self._importlib_util_proxy.__dict__.update(vars(_stdlib_util))
        self._importlib_util_proxy.find_spec = self._find_spec
        self._importlib_resources_proxy = ModuleType("importlib.resources")
        self._importlib_resources_proxy.__dict__.update(vars(_stdlib_resources))
        for function_name in (
            "files",
            "open_binary",
            "open_text",
            "read_binary",
            "read_text",
            "contents",
            "is_resource",
            "path",
        ):
            function = getattr(_stdlib_resources, function_name)
            setattr(
                self._importlib_resources_proxy,
                function_name,
                self._resource_function_proxy(
                    function,
                    infer_anchor=(
                        function_name == "files" and _accepts_no_arguments(function)
                    ),
                ),
            )
        importlib_proxy.util = self._importlib_util_proxy
        importlib_proxy.resources = self._importlib_resources_proxy
        self._importlib_proxy = importlib_proxy

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
        fromlist: tuple[str, ...] | list[str] | None = (),
        level: int = 0,
    ) -> ModuleType:
        if level == 0 and (name == "importlib" or name.startswith("importlib.")):
            return self._import_importlib(name, globals, locals, fromlist)
        private_name = self._private_module_name(name) if level == 0 else None
        if private_name is None:
            return builtins.__import__(name, globals, locals, fromlist, level)

        relative_name = self._relative_name(name)
        module = importlib.import_module(private_name)
        if fromlist:
            for item in fromlist:
                child_name = f"{name}.{item}"
                if item != "*" and self.resolve_module(child_name) is not None:
                    relative_child = self._relative_name(child_name)
                    importlib.import_module(f"{self.name}.{relative_child}")
            return module
        top_level_name = relative_name.split(".", 1)[0]
        return sys.modules[f"{self.name}.{top_level_name}"]

    def _import_importlib(
        self,
        name: str,
        globals: dict[str, Any] | None,
        locals: dict[str, Any] | None,
        fromlist: tuple[str, ...] | list[str] | None,
    ) -> ModuleType:
        imported = builtins.__import__(name, globals, locals, fromlist, 0)
        if name == "importlib":
            for item in fromlist or ():
                if item in {"*", "import_module"}:
                    continue
                if item == "util":
                    setattr(self._importlib_proxy, item, self._importlib_util_proxy)
                    continue
                if item == "resources":
                    setattr(
                        self._importlib_proxy, item, self._importlib_resources_proxy
                    )
                    continue
                try:
                    value = getattr(imported, item)
                except AttributeError:
                    continue
                setattr(self._importlib_proxy, item, value)
            return self._importlib_proxy
        child_name = name.split(".", 2)[1]
        if fromlist:
            child_module = {
                "util": self._importlib_util_proxy,
                "resources": self._importlib_resources_proxy,
            }.get(child_name)
            if child_module is not None:
                setattr(self._importlib_proxy, child_name, child_module)
                return child_module
            return imported

        child = {
            "util": self._importlib_util_proxy,
            "resources": self._importlib_resources_proxy,
        }.get(child_name) or sys.modules.get(f"importlib.{child_name}")
        if child is not None:
            setattr(self._importlib_proxy, child_name, child)
        return self._importlib_proxy

    def _find_spec(
        self,
        name: str,
        package: str | None = None,
    ) -> ModuleSpec | None:
        """Resolve extension-local names through this namespace's finder."""
        if name.startswith("."):
            private_package = self._private_package_name(package)
            if private_package is not None:
                name = importlib.util.resolve_name(name, private_package)
        private_name = self._private_module_name(name)
        if private_name is not None:
            return _stdlib_util.find_spec(private_name)
        return _stdlib_util.find_spec(name, package)

    def _resource_anchor(self, anchor: Any) -> Any:
        if isinstance(anchor, str):
            private_name = self._private_module_name(anchor)
            if private_name is not None:
                return importlib.import_module(private_name)
        if isinstance(anchor, ModuleType) and (
            anchor.__name__ == self.name or anchor.__name__.startswith(f"{self.name}.")
        ):
            return anchor
        return anchor

    def _resource_function_proxy(
        self,
        function: Callable[..., Any],
        *,
        infer_anchor: bool,
    ) -> Callable[..., Any]:
        @wraps(function)
        def proxy(*args: Any, **kwargs: Any) -> Any:
            mapped_args = args
            mapped_kwargs = kwargs
            if args:
                anchor = args[0]
                if infer_anchor and anchor is None:
                    anchor = self._resource_caller_anchor()
                mapped_args = (self._resource_anchor(anchor), *args[1:])
            elif "anchor" in kwargs:
                mapped_kwargs = dict(kwargs)
                anchor = kwargs["anchor"]
                if infer_anchor and anchor is None:
                    anchor = self._resource_caller_anchor()
                mapped_kwargs["anchor"] = self._resource_anchor(anchor)
            elif "package" in kwargs:
                mapped_kwargs = dict(kwargs)
                anchor = kwargs["package"]
                if infer_anchor and anchor is None:
                    anchor = self._resource_caller_anchor()
                mapped_kwargs["package"] = self._resource_anchor(anchor)
            elif infer_anchor:
                caller_anchor = self._resource_caller_anchor()
                if caller_anchor is not None:
                    mapped_args = (caller_anchor,)
            return function(*mapped_args, **mapped_kwargs)

        return proxy

    def _resource_caller_anchor(self) -> ModuleType | None:
        frame = sys._getframe(1)
        try:
            private_prefix = f"{self.name}."
            while frame is not None:
                module_name = frame.f_globals.get("__name__")
                if isinstance(module_name, str) and (
                    module_name == self.name or module_name.startswith(private_prefix)
                ):
                    module = sys.modules.get(module_name)
                    if isinstance(module, ModuleType):
                        return module
                frame = frame.f_back
            return None
        finally:
            del frame

    def _dynamic_import_module(
        self,
        name: str,
        package: str | None = None,
    ) -> ModuleType:
        private_prefix = f"{self.name}."
        if name == self.name or name.startswith(private_prefix):
            return importlib.import_module(name, package)

        if name.startswith("."):
            private_package = self._private_package_name(package)
            return importlib.import_module(name, private_package or package)

        private_name = self._private_module_name(name)
        if private_name is not None:
            return importlib.import_module(private_name)
        return importlib.import_module(name, package)

    def _private_module_name(self, module_name: str) -> str | None:
        if not module_name or module_name.startswith("."):
            return None
        private_prefix = f"{self.name}."
        if module_name == self.name or module_name.startswith(private_prefix):
            return module_name

        relative_name = self._relative_name(module_name)
        top_level_name = relative_name.partition(".")[0]
        if self.resolve_module(top_level_name) is None:
            return None
        return f"{self.name}.{relative_name}"

    def _private_package_name(self, package: str | None) -> str | None:
        if not package:
            return None
        if package == self.name:
            return package
        private_name = self._private_module_name(package)
        if private_name is None:
            return None
        relative_name = private_name[len(self.name) + 1 :]
        resolved = self.resolve_module(relative_name)
        if resolved is None or not resolved[1]:
            return None
        return private_name

    def _relative_name(self, module_name: str) -> str:
        prefix = self.module_prefix
        if prefix and module_name.startswith(f"{prefix}."):
            return module_name[len(prefix) + 1 :]
        return module_name


__all__ = ["ExtensionImportError", "ExtensionModuleNamespace"]
