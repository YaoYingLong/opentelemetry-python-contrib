# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""
This library allows tracing HTTP requests made by the
`requests <https://requests.readthedocs.io/en/master/>`_ library.

Usage
-----

.. code-block:: python

    import requests
    from opentelemetry.instrumentation.requests import RequestsInstrumentor

    # You can optionally pass a custom TracerProvider to instrument().
    RequestsInstrumentor().instrument()
    response = requests.get(url="https://www.example.org/")

Configuration
-------------

Request/Response hooks
**********************

The requests instrumentation supports extending tracing behavior with the help of
request and response hooks. These are functions that are called back by the instrumentation
right after a Span is created for a request and right before the span is finished processing a response respectively.
The hooks can be configured as follows:

.. code:: python

    import requests
    from opentelemetry.instrumentation.requests import RequestsInstrumentor

    # `request_obj` is an instance of requests.PreparedRequest
    def request_hook(span, request_obj):
        pass

    # `request_obj` is an instance of requests.PreparedRequest
    # `response` is an instance of requests.Response
    def response_hook(span, request_obj, response):
        pass

    RequestsInstrumentor().instrument(
        request_hook=request_hook, response_hook=response_hook
    )

Capture HTTP request and response headers
*****************************************
You can configure the agent to capture specified HTTP headers as span attributes, according to the
`semantic conventions <https://opentelemetry.io/docs/specs/semconv/http/http-spans/#http-client-span>`_.

Request headers
***************
To capture HTTP request headers as span attributes, set the environment variable
``OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST`` to a comma delimited list of HTTP header names.

For example using the environment variable,
::

    export OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST="content-type,custom_request_header"

will extract ``content-type`` and ``custom_request_header`` from the request headers and add them as span attributes.

Request header names in Requests are case-insensitive. So, giving the header name as ``CUStom-Header`` in the environment
variable will capture the header named ``custom-header``.

Regular expressions may also be used to match multiple headers that correspond to the given pattern.  For example:
::

    export OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST="Accept.*,X-.*"

Would match all request headers that start with ``Accept`` and ``X-``.

To capture all request headers, set ``OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST`` to ``".*"``.
::

    export OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST=".*"

The name of the added span attribute will follow the format ``http.request.header.<header_name>`` where ``<header_name>``
is the normalized HTTP header name (lowercase, with ``-`` replaced by ``_``). The value of the attribute will be a
single item list containing all the header values.

For example:
``http.request.header.custom_request_header = ["<value1>", "<value2>"]``

Response headers
****************
To capture HTTP response headers as span attributes, set the environment variable
``OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_RESPONSE`` to a comma delimited list of HTTP header names.

For example using the environment variable,
::

    export OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_RESPONSE="content-type,custom_response_header"

will extract ``content-type`` and ``custom_response_header`` from the response headers and add them as span attributes.

Response header names in Requests are case-insensitive. So, giving the header name as ``CUStom-Header`` in the environment
variable will capture the header named ``custom-header``.

Regular expressions may also be used to match multiple headers that correspond to the given pattern.  For example:
::

    export OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_RESPONSE="Content.*,X-.*"

Would match all response headers that start with ``Content`` and ``X-``.

To capture all response headers, set ``OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_RESPONSE`` to ``".*"``.
::

    export OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_RESPONSE=".*"

The name of the added span attribute will follow the format ``http.response.header.<header_name>`` where ``<header_name>``
is the normalized HTTP header name (lowercase, with ``-`` replaced by ``_``). The value of the attribute will be a
list containing the header values.

For example:
``http.response.header.custom_response_header = ["<value1>", "<value2>"]``

Sanitizing headers
******************
In order to prevent storing sensitive data such as personally identifiable information (PII), session keys, passwords,
etc, set the environment variable ``OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SANITIZE_FIELDS``
to a comma delimited list of HTTP header names to be sanitized.

Regexes may be used, and all header names will be matched in a case-insensitive manner.

For example using the environment variable,
::

    export OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SANITIZE_FIELDS=".*session.*,set-cookie"

will replace the value of headers such as ``session-id`` and ``set-cookie`` with ``[REDACTED]`` in the span.

Note:
    The environment variable names used to capture HTTP headers are still experimental, and thus are subject to change.

Custom Duration Histogram Boundaries
************************************
To customize the duration histogram bucket boundaries used for HTTP client request duration metrics,
you can provide a list of values when instrumenting:

.. code:: python

    import requests
    from opentelemetry.instrumentation.requests import RequestsInstrumentor

    custom_boundaries = [0.0, 5.0, 10.0, 25.0, 50.0, 100.0]

    RequestsInstrumentor().instrument(
        duration_histogram_boundaries=custom_boundaries
    )

Exclude lists
*************
To exclude certain URLs from being tracked, set the environment variable ``OTEL_PYTHON_REQUESTS_EXCLUDED_URLS``
(or ``OTEL_PYTHON_EXCLUDED_URLS`` as fallback) with comma delimited regexes representing which URLs to exclude.

For example,

::

    export OTEL_PYTHON_REQUESTS_EXCLUDED_URLS="client/.*/info,healthcheck"

will exclude requests such as ``https://site/client/123/info`` and ``https://site/xyz/healthcheck``.

API
---
"""

from __future__ import annotations

import functools
import types
from collections.abc import Callable, Collection
from timeit import default_timer
from typing import Any
from urllib.parse import urlparse

from requests.models import PreparedRequest, Response
from requests.sessions import Session
from requests.structures import CaseInsensitiveDict

from opentelemetry.instrumentation._semconv import (
    HTTP_DURATION_HISTOGRAM_BUCKETS_NEW,
    HTTP_DURATION_HISTOGRAM_BUCKETS_OLD,
    _client_duration_attrs_new,
    _client_duration_attrs_old,
    _filter_semconv_duration_attrs,
    _get_schema_url,
    _OpenTelemetrySemanticConventionStability,
    _OpenTelemetryStabilitySignalType,
    _report_new,
    _report_old,
    _set_http_host_client,
    _set_http_method,
    _set_http_net_peer_name_client,
    _set_http_network_protocol_version,
    _set_http_peer_port_client,
    _set_http_scheme,
    _set_http_url,
    _set_status,
    _StabilityMode,
)
from opentelemetry.instrumentation.instrumentor import BaseInstrumentor
from opentelemetry.instrumentation.requests.package import _instruments
from opentelemetry.instrumentation.requests.version import __version__
from opentelemetry.instrumentation.utils import (
    is_http_instrumentation_enabled,
    suppress_http_instrumentation,
)
from opentelemetry.metrics import Histogram, get_meter
from opentelemetry.propagate import inject
from opentelemetry.semconv._incubating.attributes.user_agent_attributes import (
    USER_AGENT_SYNTHETIC_TYPE,
)
from opentelemetry.semconv.attributes.error_attributes import ERROR_TYPE
from opentelemetry.semconv.attributes.network_attributes import (
    NETWORK_PEER_ADDRESS,
    NETWORK_PEER_PORT,
)
from opentelemetry.semconv.attributes.user_agent_attributes import (
    USER_AGENT_ORIGINAL,
)
from opentelemetry.semconv.metrics import MetricInstruments
from opentelemetry.semconv.metrics.http_metrics import (
    HTTP_CLIENT_REQUEST_DURATION,
)
from opentelemetry.trace import SpanKind, Tracer, get_tracer
from opentelemetry.trace.span import Span
from opentelemetry.util.http import (
    OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST,
    OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_RESPONSE,
    OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SANITIZE_FIELDS,
    ExcludeList,
    detect_synthetic_user_agent,
    get_custom_header_attributes,
    get_custom_headers,
    get_excluded_urls,
    normalise_request_header_name,
    normalise_response_header_name,
    normalize_user_agent,
    parse_excluded_urls,
    redact_url,
    sanitize_method,
)
from opentelemetry.util.http.httplib import set_ip_on_next_http_connection

# 可以通过环境变量配置REQUESTS_EXCLUDED_URLS和EXCLUDED_URLS，用于排除不需要拦截的url列表
_excluded_urls_from_env = get_excluded_urls("REQUESTS")

_RequestHookT = Callable[[Span, PreparedRequest], None] | None
_ResponseHookT = Callable[[Span, PreparedRequest, Response], None] | None


def _set_http_status_code_attribute(
    span,
    status_code,
    metric_attributes=None,
    sem_conv_opt_in_mode=_StabilityMode.DEFAULT,
):
    status_code_str = str(status_code)
    try:
        status_code = int(status_code)
    except ValueError:
        status_code = -1
    if metric_attributes is None:
        metric_attributes = {}
    # When we have durations we should set metrics only once
    # Also the decision to include status code on a histogram should
    # not be dependent on tracing decisions.
    _set_status(
        span,
        metric_attributes,
        status_code,
        status_code_str,
        server_span=False,
        sem_conv_opt_in_mode=sem_conv_opt_in_mode,
    )


# pylint: disable=unused-argument
# pylint: disable=R0915
def _instrument(
    tracer: Tracer,
    duration_histogram_old: Histogram,
    duration_histogram_new: Histogram,
    request_hook: _RequestHookT = None,
    response_hook: _ResponseHookT = None,
    excluded_urls: ExcludeList | None = None,
    sem_conv_opt_in_mode: _StabilityMode = _StabilityMode.DEFAULT,
    captured_request_headers: list[str] | None = None,
    captured_response_headers: list[str] | None = None,
    sensitive_headers: list[str] | None = None,
):
    """Enables tracing of all requests calls that go through
    :code:`requests.session.Session.request` (this includes
    :code:`requests.get`, etc.)."""

    # Since
    # https://github.com/psf/requests/commit/d72d1162142d1bf8b1b5711c664fbbd674f349d1
    # (v0.7.0, Oct 23, 2011), get, post, etc are implemented via request which
    # again, is implemented via Session.request (`Session` was named `session`
    # before v1.0.0, Dec 17, 2012, see
    # https://github.com/psf/requests/commit/4e5c4a6ab7bb0195dececdd19bb8505b872fe120)

    # 核心原理：运行时替换requests.sessions.Session.send，在原方法前后创建CLIENT Span、注入上下文传播头、采集响应信息和耗时
    # 并通过上下文中的标记抑制嵌套HTTP插桩。
    wrapped_send = Session.send

    # Session.send方法的定义：def send(self, request: PreparedRequest, **kwargs: Any) -> Response:
    # pylint: disable-msg=too-many-locals,too-many-branches
    @functools.wraps(wrapped_send)
    def instrumented_send(self: Session, request: PreparedRequest, **kwargs: Any):
        if excluded_urls and excluded_urls.url_disabled(request.url):
            return wrapped_send(self, request, **kwargs)

        def get_or_create_headers():
            request.headers = request.headers if request.headers is not None else CaseInsensitiveDict()
            return request.headers

        # 用来判断当前OpenTelemetry Context是否允许执行HTTP插桩逻辑。它的主要用途是临时抑制HTTP遥测，尤其是避免同一次请求在不同HTTP库中重复插桩
        if not is_http_instrumentation_enabled():
            return wrapped_send(self, request, **kwargs)

        # See
        # https://github.com/open-telemetry/semantic-conventions/blob/main/docs/http/http-spans.md#http-client
        method = request.method
        sanitized_method = sanitize_method(method.strip())
        span_name = get_default_span_name(sanitized_method)

        url = redact_url(request.url)

        span_attributes = {}
        _set_http_method(
            span_attributes,
            method,
            sanitized_method,
            sem_conv_opt_in_mode,
        )
        _set_http_url(span_attributes, url, sem_conv_opt_in_mode)

        # Check for synthetic user agent type
        headers = get_or_create_headers()
        user_agent_value = headers.get("User-Agent")
        user_agent = normalize_user_agent(user_agent_value)
        synthetic_type = detect_synthetic_user_agent(user_agent)
        if synthetic_type:
            span_attributes[USER_AGENT_SYNTHETIC_TYPE] = synthetic_type
        if user_agent:
            span_attributes[USER_AGENT_ORIGINAL] = user_agent
        span_attributes.update(
            get_custom_header_attributes(
                headers,
                captured_request_headers,
                sensitive_headers,
                normalise_request_header_name,
            )
        )

        metric_labels = {}
        _set_http_method(
            metric_labels,
            method,
            sanitized_method,
            sem_conv_opt_in_mode,
        )

        try:
            parsed_url = urlparse(url)
            if parsed_url.scheme:
                if _report_old(sem_conv_opt_in_mode):
                    # TODO: Support opt-in for url.scheme in new semconv
                    _set_http_scheme(metric_labels, parsed_url.scheme, sem_conv_opt_in_mode)
            if parsed_url.hostname:
                _set_http_host_client(metric_labels, parsed_url.hostname, sem_conv_opt_in_mode)
                _set_http_net_peer_name_client(metric_labels, parsed_url.hostname, sem_conv_opt_in_mode)
                if _report_new(sem_conv_opt_in_mode):
                    _set_http_host_client(
                        span_attributes,
                        parsed_url.hostname,
                        sem_conv_opt_in_mode,
                    )
                    # Use semconv library when available
                    span_attributes[NETWORK_PEER_ADDRESS] = parsed_url.hostname
            if parsed_url.port:
                _set_http_peer_port_client(metric_labels, parsed_url.port, sem_conv_opt_in_mode)
                if _report_new(sem_conv_opt_in_mode):
                    _set_http_peer_port_client(span_attributes, parsed_url.port, sem_conv_opt_in_mode)
                    # Use semconv library when available
                    span_attributes[NETWORK_PEER_PORT] = parsed_url.port
        except ValueError:
            pass

        # with进入时，调用__enter__()，__enter__()推进生成器，直到执行到yield span
        # next(self.gen)得到被yield出来的Span，__enter__()返回该Span，外层as span接收这个返回值
        # with结束会自动调用span的end方法
        with (
            # 这里的 as span，拿到的就是你那段代码中 yield span 交出来的对象
            tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT, attributes=span_attributes) as span,
            set_ip_on_next_http_connection(span),
        ):
            exception = None
            if callable(request_hook):
                request_hook(span, request)
            # 执行TextMapPropagator的inject方法，注入header如traceparent、tracestate、baggage等
            inject(headers)
            # Requests 底层通常调用 urllib3。如果两者都启用插桩，同一次请求可能经过两个插桩入口
            # 这个上下文管理器通过OpenTelemetry Context 置抑制标记，并在退出时恢复原上下文。
            # urllib3的包装入口也检查is_http_instrumentation_enabled()，因此在这个范围内会直接执行原方法
            with suppress_http_instrumentation():
                start_time = default_timer()
                try:
                    # 执行原始的Session.send方法
                    result = wrapped_send(self, request, **kwargs)  # *** PROCEED
                except Exception as exc:  # pylint: disable=W0703
                    exception = exc
                    result = getattr(exc, "response", None)
                finally:
                    # 记录耗时时间
                    elapsed_time = max(default_timer() - start_time, 0)

            if isinstance(result, Response):
                span_attributes = {}
                _set_http_status_code_attribute(
                    span,
                    result.status_code,
                    metric_labels,
                    sem_conv_opt_in_mode,
                )

                if result.raw is not None:
                    version = getattr(result.raw, "version", None)
                    if version:
                        # Only HTTP/1 is supported by requests
                        version_text = "1.1" if version == 11 else "1.0"
                        _set_http_network_protocol_version(metric_labels, version_text, sem_conv_opt_in_mode)
                        if _report_new(sem_conv_opt_in_mode):
                            _set_http_network_protocol_version(
                                span_attributes,
                                version_text,
                                sem_conv_opt_in_mode,
                            )
                span_attributes.update(
                    get_custom_header_attributes(
                        result.headers,
                        captured_response_headers,
                        sensitive_headers,
                        normalise_response_header_name,
                    )
                )
                for key, val in span_attributes.items():
                    span.set_attribute(key, val)

                if callable(response_hook):
                    response_hook(span, request, result)

            if exception is not None and _report_new(sem_conv_opt_in_mode):
                span.set_attribute(ERROR_TYPE, type(exception).__qualname__)
                metric_labels[ERROR_TYPE] = type(exception).__qualname__

            # 记录指标
            if duration_histogram_old is not None:
                duration_attrs_old = _filter_semconv_duration_attrs(
                    metric_labels,
                    _client_duration_attrs_old,
                    _client_duration_attrs_new,
                    _StabilityMode.DEFAULT,
                )
                duration_histogram_old.record(
                    max(round(elapsed_time * 1000), 0),
                    attributes=duration_attrs_old,
                )
            # 记录指标
            if duration_histogram_new is not None:
                duration_attrs_new = _filter_semconv_duration_attrs(
                    metric_labels,
                    _client_duration_attrs_old,
                    _client_duration_attrs_new,
                    _StabilityMode.HTTP,
                )
                duration_histogram_new.record(elapsed_time, attributes=duration_attrs_new)

            if exception is not None:
                raise exception.with_traceback(exception.__traceback__)

        return result

    instrumented_send.opentelemetry_instrumentation_requests_applied = True
    # 这一步非常关键，否则就不能实现装饰器的作用，这里其实相当于没有使用装饰器@的语法糖，直接手动实现的装饰器
    Session.send = instrumented_send


def _uninstrument():
    """Disables instrumentation of :code:`requests` through this module.

    Note that this only works if no other module also patches requests."""
    _uninstrument_from(Session)


def _uninstrument_from(instr_root, restore_as_bound_func: bool = False):
    for instr_func_name in ("request", "send"):
        instr_func = getattr(instr_root, instr_func_name)
        if not getattr(
            instr_func,
            "opentelemetry_instrumentation_requests_applied",
            False,
        ):
            continue

        original = instr_func.__wrapped__  # pylint:disable=no-member
        if restore_as_bound_func:
            original = types.MethodType(original, instr_root)
        setattr(instr_root, instr_func_name, original)


def get_default_span_name(method: str) -> str:
    """
    Default implementation for name_callback, returns HTTP {method_name}.
    https://opentelemetry.io/docs/reference/specification/trace/semantic_conventions/http/#name

    Args:
        method: string representing HTTP method
    Returns:
        span name
    """
    if method == "_OTHER":
        return "HTTP"
    return method


class RequestsInstrumentor(BaseInstrumentor):
    """An instrumentor for requests
    See `BaseInstrumentor`
    """

    def instrumentation_dependencies(self) -> Collection[str]:
        return _instruments

    def _instrument(self, **kwargs: Any):
        """Instruments requests module

        Args:
            **kwargs: Optional arguments
                ``tracer_provider``: a TracerProvider, defaults to global
                ``request_hook``: An optional callback that is invoked right after a span is created.
                ``response_hook``: An optional callback which is invoked right before the span is finished processing a response.
                ``excluded_urls``: A string containing a comma-delimited list of regexes used to exclude URLs from tracking
                ``duration_histogram_boundaries``: A list of float values representing the explicit bucket boundaries for the duration histogram.
        """
        # 获取HTTP的语义约定的类型
        semconv_opt_in_mode = _OpenTelemetrySemanticConventionStability._get_opentelemetry_stability_opt_in_mode(
            _OpenTelemetryStabilitySignalType.HTTP,
        )
        # 如果是旧的约定、或者同时支持新旧约定返回：https://opentelemetry.io/schemas/1.11.0
        # 如果是新的约定返回：https://opentelemetry.io/schemas/1.21.0
        schema_url = _get_schema_url(semconv_opt_in_mode)
        # 这里一般来说默认是没有传入tracer_provider，所以这里获取到的是None
        tracer_provider = kwargs.get("tracer_provider")
        tracer = get_tracer(
            __name__,
            __version__,
            tracer_provider,
            schema_url=schema_url,
        )
        excluded_urls = kwargs.get("excluded_urls")
        meter_provider = kwargs.get("meter_provider")
        duration_histogram_boundaries = kwargs.get("duration_histogram_boundaries")
        meter = get_meter(
            __name__,
            __version__,
            meter_provider,
            schema_url=schema_url,
        )
        duration_histogram_old = None
        # 如果semconv_opt_in_mode是旧的约定或者同时支持新旧约定
        if _report_old(semconv_opt_in_mode):
            #
            duration_histogram_old = meter.create_histogram(
                # 指标名称http.client.duration
                name=MetricInstruments.HTTP_CLIENT_DURATION,
                unit="ms",
                description="measures the duration of the outbound HTTP request",
                explicit_bucket_boundaries_advisory=duration_histogram_boundaries
                or HTTP_DURATION_HISTOGRAM_BUCKETS_OLD,
            )
        duration_histogram_new = None
        # 如果semconv_opt_in_mode是新的约定或者同时支持新旧约定
        if _report_new(semconv_opt_in_mode):
            duration_histogram_new = meter.create_histogram(
                # 指标名称http.client.request.duration
                name=HTTP_CLIENT_REQUEST_DURATION,
                unit="s",
                description="Duration of HTTP client requests.",
                explicit_bucket_boundaries_advisory=duration_histogram_boundaries
                or HTTP_DURATION_HISTOGRAM_BUCKETS_NEW,
            )
        _instrument(
            tracer,
            duration_histogram_old,
            duration_histogram_new,
            request_hook=kwargs.get("request_hook"),
            response_hook=kwargs.get("response_hook"),
            excluded_urls=(_excluded_urls_from_env if excluded_urls is None else parse_excluded_urls(excluded_urls)),
            sem_conv_opt_in_mode=semconv_opt_in_mode,
            captured_request_headers=get_custom_headers(OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST),
            captured_response_headers=get_custom_headers(OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_RESPONSE),
            sensitive_headers=get_custom_headers(OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SANITIZE_FIELDS),
        )

    def _uninstrument(self, **kwargs: Any):
        _uninstrument()

    @staticmethod
    def uninstrument_session(session: Session):
        """Disables instrumentation on the session object."""
        _uninstrument_from(session, restore_as_bound_func=True)
