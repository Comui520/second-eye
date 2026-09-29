"""全局领域常量：评分刻度与卖家风险等级。

这些字面量原先散落在 analysis 各解析器、services 与 web 路由中；集中定义后
Python 侧共用同一份（模板仍按值渲染）。
"""

SCORE_MIN = 1
SCORE_MAX = 10

RISK_LOW = "低"
RISK_MEDIUM = "中"
RISK_HIGH = "高"
RISK_UNKNOWN = "未知"

# 卖家风险 → 满意度权重系数（未知按中等处理）
RISK_FACTOR = {RISK_LOW: 1.0, RISK_MEDIUM: 0.5, RISK_HIGH: 0.0}
