# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


_instruments_psycopg2 = "psycopg2 >= 2.7.3.1"
_instruments_psycopg2_binary = "psycopg2-binary >= 2.7.3.1"

_instruments = ()
# 可选字段，声明可替代的目标库，满足其中一个即可
_instruments_any = (
    _instruments_psycopg2,
    _instruments_psycopg2_binary,
)

_semconv_status = "migration"
