# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# 每个插桩模块的元数据声明文件，主要说明“支持插桩哪些库、支持什么版本、具备哪些遥测能力”。它是项目约定的普通 Python 模块

# 声明目标库及支持版本
_instruments = ("requests ~= 2.0",)

# 可选字段，声明可替代的目标库，满足其中一个即可，例如 psycopg2 或 psycopg2-binary
# _instruments_any

# 声明是否支持指标，用于生成文档中的能力表
_supports_metrics = True

# 声明语义约定的开发或迁移状态，同样用于文档
_semconv_status = "migration"
