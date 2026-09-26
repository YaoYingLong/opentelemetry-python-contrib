# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

from opentelemetry.instrumentation.auto_instrumentation import initialize

# 通常由 Python 的 site 机制从搜索路径导入，从而触发自动插桩。
initialize()
