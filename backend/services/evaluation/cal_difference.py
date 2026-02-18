import os
import json
import numpy as np
from collections import defaultdict

# 你的 json 目录
DIR_PATH = "/home/loading/nlp/open-alpha-arena-bench/backend/services/evaluation/eval_results"

# 用来收集每个 metric 的所有 score
metric_scores = defaultdict(list)

# 遍历目录下所有 json 文件
for filename in os.listdir(DIR_PATH):
    if not filename.endswith(".json"):
        continue

    file_path = os.path.join(DIR_PATH, filename)
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    evaluations = data.get("evaluations", {})
    for metric_name, metric_content in evaluations.items():
        score = metric_content.get("score")
        if score is not None:
            metric_scores[metric_name].append(score)

# 计算并输出方差
print("Metric Variance Results:")
print("-" * 40)

metric_variance = {}
for metric, scores in metric_scores.items():
    if len(scores) > 1:
        var = float(np.var(scores))  # population variance
    else:
        var = 0.0  # 只有一个样本，方差定义为 0

    metric_variance[metric] = var
    print(f"{metric}: variance = {var:.6f}, count = {len(scores)}")
