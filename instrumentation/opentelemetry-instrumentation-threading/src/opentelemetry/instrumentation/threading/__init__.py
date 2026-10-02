# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
"""
Instrument threading to propagate OpenTelemetry context.

Usage
-----

.. code-block:: python

    from opentelemetry.instrumentation.threading import ThreadingInstrumentor

    ThreadingInstrumentor().instrument()

This library provides instrumentation for the `threading` module to ensure that
the OpenTelemetry context is propagated across threads. It is important to note
that this instrumentation does not produce any telemetry data on its own. It
merely ensures that the context is correctly propagated when threads are used.


When instrumented, new threads created using threading.Thread, threading.Timer,
or within futures.ThreadPoolExecutor will have the current OpenTelemetry
context attached, and this context will be re-activated in the thread's
run method or the executor's worker thread."
"""

# 让 Python 在定义函数时，无需立即找到注解中的类型名称
from __future__ import annotations

import threading
from collections.abc import Callable, Collection
from concurrent import futures
from typing import TYPE_CHECKING, Any

from wrapt import (
    wrap_function_wrapper,  # type: ignore[reportUnknownVariableType]
)

from opentelemetry import context
from opentelemetry.instrumentation.instrumentor import BaseInstrumentor
from opentelemetry.instrumentation.threading.package import _instruments
from opentelemetry.instrumentation.utils import unwrap

# 这段代码主要是给IDE和类型检查工具看的说明。Python真正运行程序时，会跳过整个if内的代码
# 对于TYPE_CHECKING，如果是Python运行程序，其值为False，跳过下面的代码，如果是类型检查工具分析代码把它视为True，分析下面的定义
if TYPE_CHECKING:
    # 导入两个用于描述类型的工具：
    # - TypeVar：表达“某个类型还没确定，但几个位置应该使用同一种类型”。
    # - Protocol：表达“一个对象应该具备哪些属性或方法”。
    from typing import Protocol, TypeVar
    # 可以把 R 理解成一个类型占位符，它不是具体的 int 或 str。
    # 目的就是说明：包装方法会保留原方法的返回值类型
    R = TypeVar("R")

    # 可以理解为把具有_otel_context属性，并且该属性的值是OpenTelemetry Context的对象，描述为HasOtelContext
    class HasOtelContext(Protocol):
        _otel_context: context.Context


class ThreadingInstrumentor(BaseInstrumentor):
    __WRAPPER_START_METHOD = "start"
    __WRAPPER_RUN_METHOD = "run"
    __WRAPPER_SUBMIT_METHOD = "submit"

    def instrumentation_dependencies(self) -> Collection[str]:
        return _instruments

    def _instrument(self, **kwargs: Any):
        self._instrument_thread()
        self._instrument_timer()
        self._instrument_thread_pool()

    def _uninstrument(self, **kwargs: Any):
        self._uninstrument_thread()
        self._uninstrument_timer()
        self._uninstrument_thread_pool()

    @staticmethod
    def _instrument_thread():
        """
        第一个wrap_function_wrapper是对Thread的start方法添加wrapper，做的事情就是给目标对象添加一个_otel_context属性
        类似于java中通过VirtualField绑定线程实例和context

        第一个wrap_function_wrapper是对Thread的run方法添加wrapper，跟java线程一样真正执行的是线程的run方法
        所以在run方法中会先判断instance是否有_otel_context，如果有就执行context.attach(instance._otel_context)
        然后再执行run方法，执行完成后再执行context.detach(token)，则两个相当于java中的context.makeCurrent和scope.close()
        """
        # 在运行时给已有函数或方法加一层包装，让你能在调用前后执行额外逻辑
        wrap_function_wrapper(
            # 持有目标属性的对象，或模块导入名称，这里是threading.Thread
            threading.Thread,
            # 需要包装的目标对象的方法，这里是threading.Thread的start方法
            ThreadingInstrumentor.__WRAPPER_START_METHOD,
            # 每次调用目标函数时执行的回调
            ThreadingInstrumentor.__wrap_threading_start,
        )
        wrap_function_wrapper(
            threading.Thread,
            ThreadingInstrumentor.__WRAPPER_RUN_METHOD,
            ThreadingInstrumentor.__wrap_threading_run,
        )

    @staticmethod
    def _instrument_timer():
        wrap_function_wrapper(
            threading.Timer,
            ThreadingInstrumentor.__WRAPPER_START_METHOD,
            ThreadingInstrumentor.__wrap_threading_start,
        )
        wrap_function_wrapper(
            threading.Timer,
            ThreadingInstrumentor.__WRAPPER_RUN_METHOD,
            ThreadingInstrumentor.__wrap_threading_run,
        )

    @staticmethod
    def _instrument_thread_pool():
        wrap_function_wrapper(
            futures.ThreadPoolExecutor,
            ThreadingInstrumentor.__WRAPPER_SUBMIT_METHOD,
            ThreadingInstrumentor.__wrap_thread_pool_submit,
        )

    @staticmethod
    def _uninstrument_thread():
        unwrap(threading.Thread, ThreadingInstrumentor.__WRAPPER_START_METHOD)
        unwrap(threading.Thread, ThreadingInstrumentor.__WRAPPER_RUN_METHOD)

    @staticmethod
    def _uninstrument_timer():
        unwrap(threading.Timer, ThreadingInstrumentor.__WRAPPER_START_METHOD)
        unwrap(threading.Timer, ThreadingInstrumentor.__WRAPPER_RUN_METHOD)

    @staticmethod
    def _uninstrument_thread_pool():
        unwrap(
            futures.ThreadPoolExecutor,
            ThreadingInstrumentor.__WRAPPER_SUBMIT_METHOD,
        )

    @staticmethod
    def __wrap_threading_start(
        # 被包装的可调用对象，通过它继续调用原逻辑
        call_wrapped: Callable[[], None],
        # 方法绑定的对象；普通函数为None
        instance: HasOtelContext,
        # 业务调用的位置参数，类型是元组
        args: tuple[()],
        # 业务调用的关键字参数，类型是字典
        kwargs: dict[str, Any],
    ) -> None:
        # 给方法绑定的对象添加_otel_context属性，如果拦截的是Thread的start方法，这里的instance其实就是thread对象
        instance._otel_context = context.get_current()
        return call_wrapped(*args, **kwargs)

    @staticmethod
    def __wrap_threading_run(
        # 被包装的可调用对象，通过它继续调用原逻辑
        # call_wrapped是一个可调用对象，返回值的类型用R表示，其中...表示这里没有限定它接收哪些参数
        # 目的就是说明：包装方法会保留原方法的返回值类型
        call_wrapped: Callable[..., R],
        # 方法绑定的对象；普通函数为None
        instance: HasOtelContext,
        # 业务调用的位置参数，类型是元组
        args: tuple[Any, ...],
        # 业务调用的关键字参数，类型是字典
        kwargs: dict[str, Any],
    ) -> R:
        token = None
        try:
            if hasattr(instance, "_otel_context"):
                # 这里其实相当于java中实现的context.makeCurrent
                token = context.attach(instance._otel_context)
            return call_wrapped(*args, **kwargs)
        finally:
            if token is not None:
                # 这里其实相当于java中的scope.close()
                context.detach(token)

    @staticmethod
    def __wrap_thread_pool_submit(
        call_wrapped: Callable[..., R],
        instance: futures.ThreadPoolExecutor,
        args: tuple[Callable[..., Any], ...],
        kwargs: dict[str, Any],
    ) -> R:
        # obtain the original function and wrapped kwargs
        original_func = args[0]
        otel_context = context.get_current()

        def wrapped_func(*func_args: Any, **func_kwargs: Any) -> R:
            token = None
            try:
                # 这里其实相当于java中实现的context.makeCurrent
                token = context.attach(otel_context)
                return original_func(*func_args, **func_kwargs)
            finally:
                # 这里其实相当于java中的scope.close()
                if token is not None:
                    context.detach(token)

        # replace the original function with the wrapped function
        # 这里其实是替换原是参数，将第一个参数添加一个wrapper
        new_args: tuple[Callable[..., Any], ...] = (wrapped_func,) + args[1:]
        return call_wrapped(*new_args, **kwargs)
