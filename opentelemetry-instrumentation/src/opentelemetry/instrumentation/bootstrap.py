# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import argparse
import logging
import sys
from subprocess import (
    PIPE,
    CalledProcessError,
    Popen,
    SubprocessError,
    check_call,
)

from packaging.requirements import Requirement

from opentelemetry.instrumentation.bootstrap_gen import (
    default_instrumentations as gen_default_instrumentations,
)
from opentelemetry.instrumentation.bootstrap_gen import (
    libraries as gen_libraries,
)
from opentelemetry.instrumentation.version import __version__
from opentelemetry.util._importlib_metadata import (
    PackageNotFoundError,
    version,
)

logger = logging.getLogger(__name__)

# 包装函数调用，捕获向外抛出的SubprocessError，补充命令和包名信息，再转换成 RuntimeError
def _syscall(func):
    def wrapper(package=None):
        try:
            if package:
                return func(package)
            return func()
        except SubprocessError as exp:
            # 读取异常对象 exp 的 cmd 属性；如果该属性不存在，就返回 None
            cmd = getattr(exp, "cmd", None)
            if cmd:
                msg = f'Error calling system command "{" ".join(cmd)}"'
            if package:
                msg = f'{msg} for package "{package}"'
            raise RuntimeError(msg)

    return wrapper

# _syscall装饰器的做哟个：包装函数调用，捕获向外抛出的SubprocessError，补充命令和包名信息，再转换成 RuntimeError
@_syscall
def _sys_pip_install(package):
    # explicit upgrade strategy to override potential pip config
    try:
        # 启动一个子进程，用当前Python解释器运行pip，安装或升级package，并且只在必要时调整它的依赖包
        # 假设sys.executable = "/project/.venv/bin/python"
        # 假设package = "opentelemetry-instrumentation-requests==0.67b0.dev"
        # 相当于执行：
        # /project/.venv/bin/python -m pip install -U \
        #   --upgrade-strategy only-if-needed \
        #   "opentelemetry-instrumentation-requests==0.67b0.dev"
        check_call( # 执行外部命令、等待结束，并检查退出码
            [
                sys.executable,         # 当前运行这段代码的 Python 解释器路径
                "-m",
                "pip",
                "install",              # 执行 pip 的安装操作，包括解析和安装依赖
                "-U",                   # 即 --upgrade，允许更新明确指定的目标包
                "--upgrade-strategy",   # 指定如何处理依赖包的升级
                "only-if-needed",       # 已安装的依赖满足要求时保留；不满足时才调整
                package,                # 要安装的包描述，可以包含版本约束
            ]
        )
    except CalledProcessError as error:
        print(error)

# 是安装插桩包后检查依赖是否存在冲突，只对相关包的问题报错
def _pip_check(libraries):
    """Ensures none of the instrumentations have dependency conflicts.
    Clean check reported as:
    'No broken requirements found.'
    Dependency conflicts are reported as:
    'opentelemetry-instrumentation-flask 1.0.1 has requirement opentelemetry-sdk<2.0,>=1.0, but you have opentelemetry-sdk 0.5.'
    To not be too restrictive, we'll only check for relevant packages.
    """

    """
    用当前Python解释器启动pip, sys.executable指向运行bootstrap的解释器
    例如：/project/.venv/bin/python -m pip check
    这样检查的是该解释器对应的包环境，避免直接执行 pip 时找到其他环境的命令
    
    让 pip 检查已安装包的依赖元数据：pip读取各包声明的依赖及版本约束，再核对依赖是否安装、已安装版本是否满足要求。
    例如，A 要求 B>=2，但环境中只有 B==1，就会报告冲突。这是元数据检查，不会通过运行应用来验证兼容性。
    
    收集检查报告：stdout=PIPE捕获标准输出；communicate()读取输出并等待子进程结束；[0]取标准输出，.decode()将字节转换成字符串。
    正常报告为：No broken requirements found.
    
    输出和待匹配字符串都转换成小写，进行不区分大小写的子串匹配。命中后抛出 RuntimeError，异常包含完整的 pip 检查报告；
    没有命中则正常返回 None。当前函数没有检查子进程退出码，因此 pip 报错不一定导致 bootstrap 报错。
    """

    with Popen([sys.executable, "-m", "pip", "check"], stdout=PIPE) as check_pipe:
        pip_check = check_pipe.communicate()[0].decode()
        pip_check_lower = pip_check.lower()
    for package_tup in libraries:
        for package in package_tup:
            if package.lower() in pip_check_lower:
                raise RuntimeError(f"Dependency conflict found: {pip_check}")


def _is_installed(req):
    req = Requirement(req)

    try:
        dist_version = version(req.name)
    except PackageNotFoundError:
        return False

    if not req.specifier.filter(dist_version):
        logger.warning(
            "instrumentation for package %s is available but version %s is installed. Skipping.",
            req,
            dist_version,
        )
        return False
    return True


def _find_installed_libraries(default_instrumentations, libraries):
    # 把default_instrumentations中的元素逐个交给调用方，这里其实是利用yield产生一个生成器
    # 下游通过for、next() 或 join() 消费它时，函数才执行。每产生一个元素就暂停；
    # 调用方索取下一个元素时再继续。默认列表全部输出后，才进入后面的库检测循环。
    # yield from default_instrumentations语法上相当于下面的写法
    # for instrumentation in default_instrumentations:
    #     yield instrumentation
    yield from default_instrumentations

    # 检查libraries，输出当前环境已经安装且版本匹配的库所对应的插桩包
    for lib in libraries:
        # lib["library"]获取的内容例子：requests ~= 2.0
        if _is_installed(lib["library"]):
            # lib["instrumentation"]获取的内容例子：opentelemetry-instrumentation-requests==0.67b0.dev
            yield lib["instrumentation"]


def _run_requirements(default_instrumentations, libraries):
    logger.setLevel(logging.ERROR)
    print("\n".join(_find_installed_libraries(default_instrumentations, libraries)))


def _run_install(default_instrumentations, libraries):
    for lib in _find_installed_libraries(default_instrumentations, libraries):
        _sys_pip_install(lib)
    _pip_check(libraries)


# pyproject.toml 将 opentelemetry-bootstrap 命令注册到此入口。
# 在接入时一般使用opentelemetry-bootstrap -a install来安装依赖
def run(
    # default_instrumentations 默认加入，如logging、sqlite3、threading、urllib等
    # logging、threading等属于标准库，通常没有独立的pip distribution元数据
    default_instrumentations: list | None = None,
    # libraries 根据当前环境是否安装目标库来选择
    # 以requests为例，它是第三方包，可以通过distribution元数据检查是否安装。若应用没安装requests，就无必要安装对应的插桩包
    libraries: list | None = None,
) -> None:
    action_install = "install"
    action_requirements = "requirements"

    parser = argparse.ArgumentParser(
        description="""
        opentelemetry-bootstrap detects installed libraries and automatically
        installs the relevant instrumentation packages for them.
        """
    )
    parser.add_argument(
        "--version",
        help="print version information",
        action="version",
        version="%(prog)s " + __version__,
    )
    # 默认输出依赖列表；-a install 才会安装插桩包。
    parser.add_argument(
        "-a",
        "--action",
        choices=[action_install, action_requirements],
        default=action_requirements,
        help="""
        install - uses pip to install the new requirements using to the
                  currently active site-package.
        requirements - prints out the new requirements to stdout. Action can
                       be piped and appended to a requirements.txt file.
        """,
    )
    args = parser.parse_args()

    if libraries is None:
        # 生成的第三方库与对应插桩包的映射。gen_libraries是定义在bootstrap_gen.py中
        libraries = gen_libraries

    if default_instrumentations is None:
        # 无需检测第三方库即可加入结果的默认插桩包。
        default_instrumentations = gen_default_instrumentations

    # requirements 输出包名，install 安装匹配的包并检查依赖冲突。这里其实就是根据传入的参数从字典中取出对应的函数
    cmd = {
        action_install: _run_install,
        action_requirements: _run_requirements,
    }[args.action]
    cmd(default_instrumentations, libraries)
