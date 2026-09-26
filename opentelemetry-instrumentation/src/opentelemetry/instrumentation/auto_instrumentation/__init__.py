# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from argparse import REMAINDER, ArgumentParser
from logging import getLogger
from os import environ, execl, getcwd
from os.path import abspath, dirname, pathsep
from re import sub
from shutil import which

from opentelemetry.instrumentation.auto_instrumentation._load import (
    _load_configurators,
    _load_distro,
    _load_instrumentors,
)
from opentelemetry.instrumentation.environment_variables import (
    OTEL_PYTHON_AUTO_INSTRUMENTATION_EXPERIMENTAL_GEVENT_PATCH,
)
from opentelemetry.instrumentation.utils import _python_path_without_directory
from opentelemetry.instrumentation.version import __version__
from opentelemetry.util._importlib_metadata import entry_points

_logger = getLogger(__name__)


# pyproject.toml 将 opentelemetry-instrument 命令映射到此入口。
def run() -> None:
    # 创建解析器实例
    parser = ArgumentParser(
        description="""
        opentelemetry-instrument automatically instruments a Python
        program and its dependencies and then runs the program.
        """,
        epilog="""
        Optional arguments (except for --help and --version) for opentelemetry-instrument
        directly correspond with OpenTelemetry environment variables. The
        corresponding optional argument is formed by removing the OTEL_ or
        OTEL_PYTHON_ prefix from the environment variable and lower casing the
        rest. For example, the optional argument --attribute_value_length_limit
        corresponds with the environment variable
        OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT.

        These optional arguments will override the current value of the
        corresponding environment variable during the execution of the command.
        """,
    )

    argument_otel_environment_variable = {}

    # 从环境变量入口点注册对应的命令行参数。
    # 在pyproject.toml中配置了entry_points的opentelemetry_environment_variables对应的文件为environment_variables.py
    # Python的entry point是安装包写入的一张名称 → Python对象索引。它负责发现对象；是否导入、实例化和执行，由使用这张索引的代码决定。
    for entry_point in entry_points(group="opentelemetry_environment_variables"):
        # 加载environment_variables.py中所有的内容
        environment_variable_module = entry_point.load()
        # 遍历environment_variable_module对应的目录
        for attribute in dir(environment_variable_module):
            if attribute.startswith("OTEL_"):
                # 把匹配到OTEL_或OTEL_PYTHON_前缀替换为空字符串，之后链式调用lower()全部转小写
                argument = sub(r"OTEL_(PYTHON_)?", "", attribute).lower()
                # 注册参数
                parser.add_argument(
                    f"--{argument}",
                    required=False,
                )
                argument_otel_environment_variable[argument] = attribute
    # 注册参数
    parser.add_argument(
        "--version",
        help="print version information",
        action="version",
        version="%(prog)s " + __version__,
    )
    # 比如：启动命令为opentelemetry-instrument --service-name demo python app.py --port 8000 --debug
    # 位置参数command：第一个位置参数，捕获1个值，代表要执行的程序，这里command获取到的值就是python
    parser.add_argument("command", help="Your Python application.")
    # 位置参数command_args：捕获剩下全部所有参数放进列表，是传给这个程序的参数，这里command_args获取到的是app.py --port 8000 --debug
    parser.add_argument(
        "command_args",
        help="Arguments for your application.",
        nargs=REMAINDER,
    )
    # 从 `sys.argv` 提取参数，返回命名空间对象（`args.xxx`访问）
    args = parser.parse_args()
    # 遍历上面从environment_variables.py中获取的映射关系
    for argument, otel_environment_variable in (argument_otel_environment_variable).items():
        # 其实就是解析用户的执行命令上的参数，获取具体值
        value = getattr(args, argument)
        if value is not None:
            # 对应值不为空，需要设置到环境变量中
            environ[otel_environment_variable] = value
    # 读取操作系统环境变量PYTHONPATH
    # 1. 读取原来的 `PYTHONPATH` 保存为旧值
    # 2. 把 otel 自动插桩的源码目录**插入到 PYTHONPATH 最前面**，保证优先加载 otel 的 patch 代码
    # 3. 执行用户命令（`args.command` 也就是 `python app.py`）的时候，把新的 PYTHONPATH 传给子进程环境
    # 4. 子进程启动，import 模块时优先找到 otel 插桩代码，实现自动 monkey patch
    python_path = environ.get("PYTHONPATH")

    if not python_path:
        python_path = []

    else:
        # 按照分号（window系统）或冒号（Linux或Mac系统）分割
        python_path = python_path.split(pathsep)

    # 获取的是当前工作目录
    # 比如在在 D:\code\otel-project执行命令opentelemetry-instrument python app.py，这里获取到的就是D:\code\otel-project
    cwd_path = getcwd()

    # This is being added to support applications that are being run from their
    # own executable, like Django.
    # FIXME investigate if there is another way to achieve this
    if cwd_path not in python_path:
        # 把 `cwd_path`（通常是当前目录的路径）插入到列表 `python_path` 的最前面（索引 0 位置）
        python_path.insert(0, cwd_path)

    # 获取当前Python脚本文件所在目录的绝对路径
    # 其实就是opentelemetry-instrumentation/src/opentelemetry/instrumentation/auto_instrumentation目录的绝对路径
    # 这里的目的就是将sitecustomize.py添加到PYTHONPATH中
    # 目标Python解释器启动→site导入sitecustomize，sitecustomize.py调用initialize()
    filedir_path = dirname(abspath(__file__))
    # 过滤掉python_path中路径等于filedir_path的
    python_path = [path for path in python_path if path != filedir_path]

    python_path.insert(0, filedir_path)
    # 来自os模块，是当前操作系统用来分隔多个路径的字符，window上是; Linux或Mac是:
    environ["PYTHONPATH"] = pathsep.join(python_path)

    executable = which(args.command)
    # 用目标命令替换当前进程，保留上面设置的环境变量和 PYTHONPATH。
    execl(executable, executable, *args.command_args)


def _initialize(*, swallow_exceptions: bool = True) -> None:
    # handle optional gevent monkey patching. This is done via environment variables so it may be used from the
    # opentelemetry operator
    gevent_patch: str | None = environ.get(OTEL_PYTHON_AUTO_INSTRUMENTATION_EXPERIMENTAL_GEVENT_PATCH)
    if gevent_patch is not None:
        if gevent_patch != "patch_all":
            _logger.error(
                "%s value must be `patch_all`",
                OTEL_PYTHON_AUTO_INSTRUMENTATION_EXPERIMENTAL_GEVENT_PATCH,
            )
        else:
            try:
                # pylint: disable=import-outside-toplevel
                from gevent import monkey  # noqa: PLC0415

                getattr(monkey, gevent_patch)()
            except ImportError:
                _logger.exception("Failed to monkey patch with gevent because gevent is not available")
                if not swallow_exceptions:
                    raise

    try:
        # 加载所选Distro，没有配置时默认是OpenTelemetryDistro将exporter设置otlp，兜底使用DefaultDistro也就是什么也不做
        distro = _load_distro()
        # 由Distro设置发行版所需的默认配置，真正调用BaseDistro实现类的configure完成配置设置
        distro.configure()
        # 加载所选 Configurator 并执行其配置。
        _load_configurators()
        # 加载已安装且依赖满足的插桩入口点。
        _load_instrumentors(distro)
    except Exception as exc:  # pylint: disable=broad-except
        _logger.exception("Failed to auto initialize OpenTelemetry")
        if not swallow_exceptions:
            raise exc


def initialize(*, swallow_exceptions: bool = True) -> None:
    """
    Setup auto-instrumentation, called by the sitecustomize module

    :param swallow_exceptions: Whether or not to propagate instrumentation exceptions to the caller. Exceptions are logged and swallowed by default.
    """
    filedir = dirname(abspath(__file__))

    python_path = environ.get("PYTHONPATH")
    auto_instrumentation_path_was_present = python_path is not None and filedir in python_path.split(pathsep)

    # Remove the auto-instrumentation path during initialization to prevent
    # auto-instrumentation from executing in subprocesses spawned during this phase.
    # This suppression is performed to avoid creating a recursive loop scenario
    # where subprocesses spawned in the initialization phase execute the
    # initialization phase again, spawning more subprocesses.
    if python_path is not None:
        # 使用当前模块的绝对目录，从 PYTHONPATH 中暂时移除自动插桩入口。
        environ["PYTHONPATH"] = _python_path_without_directory(python_path, filedir, pathsep)

    try:
        _initialize(swallow_exceptions=swallow_exceptions)
    finally:
        if auto_instrumentation_path_was_present:
            current = environ.get("PYTHONPATH", "")
            if filedir not in current.split(pathsep):
                environ["PYTHONPATH"] = filedir + pathsep + current if current else filedir
