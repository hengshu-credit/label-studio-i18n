# 本地 Transformers 主动学习

支持 Hugging Face 缓存模型 ID，以及 `save_pretrained()` 保存的本地模型目录。分类使用 `AutoModelForSequenceClassification`；实体识别使用 `AutoModelForTokenClassification` 和 BIO 标签。目录需包含 config、fast tokenizer 和 safetensors 权重。模型必须兼容所选任务的 AutoModel 类；Embedding、生成式聊天模型及 GLiNER 的专用权重不能直接作为这两类模型使用。需要先转换或训练对应任务头。

服务只读取本地文件或已有 Hugging Face 缓存，不在标注请求中下载模型，不启用 `trust_remote_code`。路径属于推理服务器：本地 Windows 服务填写 Windows 路径；远程服务填写远程机器路径。模型按连接、项目、标签配置及基座文件状态隔离。

## 启动

Windows 工作区沿用 Anaconda base，保留已安装的 CUDA PyTorch；不创建新环境。已有依赖时无需重复安装。

```powershell
& "C:\Users\Administrator\anaconda3\python.exe" -m pip install -r "E:\workspace\兼职\label-studio\label-studio-i18n\ml_backend\requirements.txt"
& "C:\Users\Administrator\anaconda3\python.exe" "E:\workspace\兼职\label-studio\label-studio-i18n\ml_backend\transformers_service.py" --port 9092
```

服务默认仅监听回环地址，单进程串行执行模型推理及训练。后台地址为 `http://host.docker.internal:9092`，Compose 的 `ML_BLOCK_LOCAL_IP` 需设为 `false` 才能连接本机。无需关闭全局 webhook 防护；Transformers 连接不创建通用训练 webhook。

## 后台配置

1. 项目设置 → 模型 → 添加模型，填写名称、后端 URL、Transformers 模型 ID 或本地目录、任务类型及可选 revision。
2. 项目存在多个同类型标注控件时，填写 XML 中的控件 `name`。分类和 NER 分别建立连接，可各用不同的模型目录。
3. 模型 `config.json` 的 `id2label` 须与项目分类标签完全匹配。NER 须为 `O`、每类的 `B-标签` 和 `I-标签`。已有模型标签不同时，可在额外参数填写 `label_map`，例如 `{"label_map":{"LABEL_0":"正常","LABEL_1":"异常"}}`；NER 映射包含完整 BIO 前缀。
4. 使用纯基座且要新建任务头时，明确设置 `initialize_head: true`。该模式初次训练前不会返回随机头的预标注；先手工完成足够的标注。已有训练模型保持此参数为 false，避免重置标签头。
5. 打开启用自动主动学习，关闭交互式预标注。项目设置 → 标注 → 预标注选择该连接名称；项目设置 → 常规 → 任务采样选择不确定性采样。通过“标注所有任务”使用采样队列，按当前表格顺序标注仍尊重显式排序。
6. Compose 中的 `active-learning` 服务每 15 秒检查人工标注并维护候选池。模型卡显示最近检查、最近训练及失败原因；刷新页面获取状态。未启动 worker 时显示等待状态。

额外参数示例（可按项目分别配置）：

```json
{
  "min_training_samples": 10,
  "train_every": 10,
  "candidate_pool_size": 32,
  "epochs": 1,
  "learning_rate": 0.00002,
  "max_length": 512,
  "initialize_head": false
}
```

分类和实体识别都能训练；一次选择一个连接作为当前预标注及不确定性评分来源。不会自动合并多个模型的预测。旧短信 Qwen3/GLiNER 连接仍保持独立，可继续选择。

## 闭环与边界

工作进程检查已提交人工标注，达到阈值后完整回放训练集，在基座副本上执行 AdamW 梯度更新。新增标注按 `train_every` 分批触发；现有标注修改或删除也会触发后续检查。手动开始训练用于请求下一轮，仍需满足最小样本数。取消标注、草稿和 predictions 不作为训练标签。

训练完成后将模型及 tokenizer 保存至服务的 `data/<配置标识>/<版本>/`，原子切换当前版本指针；原基座和历史 checkpoint 保留。训练失败保留上一版本并在模型卡显示错误；下一轮重试。无变化的训练集不会反复训练。生产使用应保留原始基座并定期备份整个模型输出目录和 Label Studio 数据卷。

候选池默认 32 条，上限 512，只对池中的未完成任务进行当前版本推理。标注后补充候选，新版本生成新候选预测，旧预测保留可追溯。采样按所选模型当前版本的 score 升序，忽略空分数。**这是候选池内的主动学习，不是对全部 481 万条短信进行全局最不确定性搜索。** 分类 score 为最高类别 softmax，NER 为最不确定真实 token 的最大概率；它们不是经过校准的准确率。

当前支持单标签文本分类及不重叠 BIO 实体；使用完整原文字符偏移。不支持区域级/条件控件、远程 URL 文本、多标签分类和重叠实体。超出模型 token 上限、实体边界切开 token、标签映射不匹配会报错，不截断或丢弃实体。超过 10000 份人工标注时停止内置训练并提示使用外部训练流程；不静默抽样。训练是一条文本一个优化步骤，全参数训练大模型可能超过显存，失败时需更换更小模型或使用外部训练后端。

## 验证

`test_transformers_service.py` 在本机生成两个小型 BERT safetensors 模型，实际执行分类/NER 推理和训练，验证权重变化、版本保存、进程恢复、项目隔离、标签及字符边界；不下载模型。CI 在 CPU 上运行相同测试。测试不代表用户业务模型的准确率；实际业务模型需配置后单独评估。

```powershell
& "C:\Users\Administrator\anaconda3\python.exe" -m unittest discover -s ml_backend -p "test_*.py" -v
```
