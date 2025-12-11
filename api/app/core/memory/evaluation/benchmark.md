⏬数据集下载地址：
    Locomo10.json：https://github.com/snap-research/locomo/tree/main/data
    LongMemEval_oracle.json：https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned
    msc_self_instruct.jsonl:https://huggingface.co/datasets/MemGPT/MSC-Self-Instruct
    上方数据集下载好后全部放入app/core/memory/data文件夹中

全流程基准测试运行：
    locomo：
        python -m app.core.memory.evaluation.run_eval --dataset locomo --sample-size 1 --reset-group --group-id yyw1 --search-type hybrid --search-limit 8 --context-char-budget 12000 --llm-max-tokens 32
    LongMemEval：
        python -m app.core.memory.evaluation.run_eval --dataset longmemeval --sample-size 10 --start-index 0 --group-id longmemeval_zh_bak_2 --search-limit 8 --context-char-budget 4000 --search-type hybrid --max-contexts-per-item 2 --reset-group
    memsciqa：
        python -m app.core.memory.evaluation.run_eval --dataset memsciqa --sample-size 10 --reset-group --group-id group_memsci

单独检索评估运行命令：
    python -m app.core.memory.evaluation.locomo.locomo_test
    python -m app.core.memory.evaluation.longmemeval.test_eval
    python -m app.core.memory.evaluation.memsciqa.memsciqa-test
    需要先在项目中修改需要检测评估的group_id。

参数及解释：
    ● --dataset longmemeval - 指定数据集
    ● --sample-size 10 - 评估10个样本
    ● --start-index 0 - 从第0个样本开始
    ● --group-id longmemeval_zh_bak_2 - 使用指定的组ID
    ● --search-limit 8 - 检索限制8条
    ● --context-char-budget 4000 - 上下文字符预算4000
    ● --search-type hybrid - 使用混合检索
    ● --max-contexts-per-item 2 - 每个样本最多摄入2个上下文
    ● --reset-group - 运行前清空组数据