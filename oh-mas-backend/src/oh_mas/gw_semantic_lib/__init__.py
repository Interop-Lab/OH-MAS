"""
GW Semantic Library - ArkTS/C++ 单文件结构分析器

P1 实现：不构建全图，只针对告警文件做单文件 AST 分析。
用正则代替 tree-sitter 解析 ArkTS struct，原因：
  - tree-sitter-typescript 不识别 ArkTS 的 struct 关键字，struct body 整体进 ERROR 节点
  - 实测 7538 个 .ets 文件，修复版正则覆盖率 99.5%
  - tree-sitter 保留用于 import/export 语句解析（build_full_graphs.py 已验证）
"""

from oh_mas.gw_semantic_lib.arkts_analyzer import ArkTSAnalyzer, ComponentInfo, MethodInfo, PropInfo

__all__ = ["ArkTSAnalyzer", "ComponentInfo", "MethodInfo", "PropInfo"]
