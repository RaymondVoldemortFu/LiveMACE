import json
import os
import statistics
from collections import defaultdict

DIR_PATH = "/home/loading/nlp/open-alpha-arena-bench/backend/services/evaluation/eval_results/account_2"

# 存每个 metric 在不同文件中的 mean
# 结构: { "metric_a": [mean1, mean2, mean3], ... }
metric_means = defaultdict(list)

# 遍历目录中的 json 文件
for filename in os.listdir(DIR_PATH):
    if not filename.endswith(".json"):
        continue

    file_path = os.path.join(DIR_PATH, filename)
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "metric_statistics" not in data:
        raise KeyError(f"{filename} 中缺少 metric_statistics")

    for metric_name, metric_info in data["metric_statistics"].items():
        if "mean" not in metric_info:
            raise KeyError(f"{filename} 中 {metric_name} 缺少 mean")
        metric_means[metric_name].append(metric_info["mean"])

# 计算每个 metric 的方差
metric_variances = {}

for metric_name, means in metric_means.items():
    if len(means) < 2:
        raise ValueError(f"{metric_name} 文件数不足，无法计算方差")

    # 总体方差（更符合“多个实验结果”的语义）
    metric_variances[metric_name] = statistics.pvariance(means)

# 输出
print("metric_a ~ metric_f 在不同文件中 mean 的方差：")
for metric in sorted(metric_variances):
    print(f"{metric}: {metric_variances[metric]}")
