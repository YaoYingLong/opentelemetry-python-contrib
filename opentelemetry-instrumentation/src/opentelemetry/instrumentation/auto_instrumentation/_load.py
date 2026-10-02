# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from functools import cached_property
from logging import getLogger
from os import environ

from opentelemetry.instrumentation.dependencies import (
    DependencyConflictError,
    get_dist_dependency_conflicts,
)
from opentelemetry.instrumentation.distro import BaseDistro, DefaultDistro
from opentelemetry.instrumentation.environment_variables import (
    OTEL_PYTHON_CONFIGURATOR,
    OTEL_PYTHON_DISABLED_INSTRUMENTATIONS,
    OTEL_PYTHON_DISTRO,
)
from opentelemetry.instrumentation.version import __version__
from opentelemetry.util._importlib_metadata import (
    EntryPoint,
    distributions,
    entry_points,
)

_logger = getLogger(__name__)

SKIPPED_INSTRUMENTATIONS_WILDCARD = "*"


class _EntryPointDistFinder:
    @cached_property
    def _mapping(self):
        return {self._key_for(ep): dist for dist in distributions() for ep in dist.entry_points}

    def dist_for(self, entry_point: EntryPoint):
        dist = getattr(entry_point, "dist", None)
        if dist:
            return dist

        return self._mapping.get(self._key_for(entry_point))

    @staticmethod
    def _key_for(entry_point: EntryPoint):
        return f"{entry_point.group}:{entry_point.name}:{entry_point.value}"


# 从 opentelemetry_distro 入口点选择 Distro，未找到时使用 DefaultDistro。
def _load_distro() -> BaseDistro:
    # 从环境变量获取OTEL_PYTHON_DISTRO对应的值
    distro_name = environ.get(OTEL_PYTHON_DISTRO, None)
    # 这里其实就是获取的opentelemetry_distro模块中pyproject.toml中定义的project.entry-points.opentelemetry_distro
    # 所以获取到的entry_point默认是opentelemetry.distro:OpenTelemetryDistro
    for entry_point in entry_points(group="opentelemetry_distro"):
        try:
            # If no distro is specified, use first to come up.
            if distro_name is None or distro_name == entry_point.name:
                distro = entry_point.load()()
                # 这里判断如果distro不是opentelemetry.instrumentation.distro:BaseDistro实例会输出日志，并跳过该distro
                if not isinstance(distro, BaseDistro):
                    _logger.debug(
                        "%s is not an OpenTelemetry Distro. Skipping",
                        entry_point.name,
                    )
                    continue
                _logger.debug("Distribution %s will be configured", entry_point.name)
                # 这里返回的是opentelemetry.distro:OpenTelemetryDistro实例
                return distro
        except Exception as exc:  # pylint: disable=broad-except
            _logger.exception("Distribution %s configuration failed", entry_point.name)
            raise exc
    # 这里返回是opentelemetry.instrumentation.distro:DefaultDistro实例
    return DefaultDistro()


def _load_instrumentors(distro):
    # 从OTEL_PYTHON_DISABLED_INSTRUMENTATIONS环境变量中获取需要排除的组件列表
    package_to_exclude = environ.get(OTEL_PYTHON_DISABLED_INSTRUMENTATIONS, [])
    entry_point_finder = _EntryPointDistFinder()
    if isinstance(package_to_exclude, str):
        # 将获取到的排除的组件列表按照逗号分割成数组
        package_to_exclude = package_to_exclude.split(",")
        # to handle users entering "requests , flask" or "requests, flask" with spaces
        # 将每一项去掉空格
        package_to_exclude = [x.strip() for x in package_to_exclude]

    # 遍历执行所有的opentelemetry_pre_instrument，目前并没有任何组件中定义了
    for entry_point in entry_points(group="opentelemetry_pre_instrument"):
        entry_point.load()()

    # 遍历执行所有的opentelemetry_pre_instrument
    for entry_point in entry_points(group="opentelemetry_instrumentor"):
        # 如果排除列表中定义了*，表示排除所有组件的插桩，那直接退出
        if SKIPPED_INSTRUMENTATIONS_WILDCARD in package_to_exclude:
            break

        # 如果加载的组件包含在需要排除的组件列表中，打印日志直接跳过
        if entry_point.name in package_to_exclude:
            _logger.debug("Instrumentation skipped for library %s", entry_point.name)
            continue

        try:
            entry_point_dist = entry_point_finder.dist_for(entry_point)
            conflict = get_dist_dependency_conflicts(entry_point_dist)
            # 如果存在依赖冲突的组件也直接跳过
            if conflict:
                conflict._log(_logger, entry_point.name)
                continue

            # tell instrumentation to not run dep checks again as we already did it above
            # 这里其实是调用的BaseDistro的load_instrumentor方法；调用具体instrumentor().instrument(**kwargs)方法
            distro.load_instrumentor(entry_point, skip_dep_check=True)
            _logger.debug("Instrumented %s", entry_point.name)
        except DependencyConflictError as exc:
            # Dependency conflicts are generally caught from get_dist_dependency_conflicts
            # returning a DependencyConflict. Keeping this error handling in case custom
            # distro and instrumentor behavior raises a DependencyConflictError later.
            # See https://github.com/open-telemetry/opentelemetry-python-contrib/pull/3610
            exc.conflict._log(_logger, entry_point.name)
            continue
        except ModuleNotFoundError as exc:
            # ModuleNotFoundError is raised when the library is not installed
            # and the instrumentation is not required to be loaded.
            # See https://github.com/open-telemetry/opentelemetry-python-contrib/issues/3421
            _logger.debug("Skipping instrumentation %s: %s", entry_point.name, exc.msg)
            continue
        except ImportError:
            # in scenarios using the kubernetes operator to do autoinstrumentation some
            # instrumentors (usually requiring binary extensions) may fail to load
            # because the injected autoinstrumentation code does not match the application
            # environment regarding python version, libc, etc... In this case it's better
            # to skip the single instrumentation rather than failing to load everything
            # so treat differently ImportError than the rest of exceptions
            _logger.exception("Importing of %s failed, skipping it", entry_point.name)
            continue
        except Exception as exc:  # pylint: disable=broad-except
            _logger.exception("Instrumenting of %s failed", entry_point.name)
            raise exc

    # 遍历执行所有的opentelemetry_post_instrument，目前并没有任何组件中定义了
    for entry_point in entry_points(group="opentelemetry_post_instrument"):
        entry_point.load()()


def _load_configurators():
    # 从环境变量获取OTEL_PYTHON_CONFIGURATOR对应的值
    configurator_name = environ.get(OTEL_PYTHON_CONFIGURATOR, None)
    configured = None
    # 这里其实就是获取的opentelemetry_distro模块中pyproject.toml中定义的opentelemetry.distro:OpenTelemetryConfigurator
    for entry_point in entry_points(group="opentelemetry_configurator"):
        if configured is not None:
            _logger.warning(
                "Configuration of %s not loaded, %s already loaded",
                entry_point.name,
                configured,
            )
            continue
        try:
            if configurator_name is None or configurator_name == entry_point.name:
                # 实例化opentelemetry.distro:OpenTelemetryConfigurator执行其configure方法，这里其实是执行_OTelSDKConfigurator中的configure方法
                # 如果环境变量中有指定OTEL_CONFIG_FILE配置文件，需要解析配置文件，一般都没有指定
                entry_point.load()().configure(auto_instrumentation_version=__version__)  # type: ignore
                configured = entry_point.name
            else:
                _logger.warning(
                    "Configuration of %s not loaded because %s is set by %s",
                    entry_point.name,
                    configurator_name,
                    OTEL_PYTHON_CONFIGURATOR,
                )
        except Exception as exc:  # pylint: disable=broad-except
            _logger.exception("Configuration of %s failed", entry_point.name)
            raise exc
