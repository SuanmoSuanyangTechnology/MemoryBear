"""
场景特定配置 - 为不同场景提供定制化的剪枝规则

功能：
- 场景特定的重要信息识别模式
- 场景特定的重要性评分权重
- 场景特定的填充词库
- 场景特定的问答对识别规则
"""

from typing import Dict, List, Set, Tuple
from dataclasses import dataclass, field


@dataclass
class ScenePatterns:
    """场景特定的识别模式"""
    
    # 重要信息的正则模式（优先级从高到低）
    high_priority_patterns: List[Tuple[str, int]] = field(default_factory=list)  # (pattern, weight)
    medium_priority_patterns: List[Tuple[str, int]] = field(default_factory=list)
    low_priority_patterns: List[Tuple[str, int]] = field(default_factory=list)
    
    # 填充词库（无意义对话）
    filler_phrases: Set[str] = field(default_factory=set)
    
    # 问句关键词（用于识别问答对）
    question_keywords: Set[str] = field(default_factory=set)
    
    # 决策性/承诺性关键词
    decision_keywords: Set[str] = field(default_factory=set)


class SceneConfigRegistry:
    """场景配置注册表 - 管理所有场景的特定配置"""
    
    # 基础通用模式（所有场景共享）
    BASE_HIGH_PRIORITY = [
        (r"订单号|工单|申请号|编号|ID|账号|账户", 5),
        (r"金额|费用|价格|¥|￥|\d+元", 5),
        (r"\d{11}", 4),  # 手机号
        (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", 4),  # 邮箱
    ]
    
    BASE_MEDIUM_PRIORITY = [
        (r"\d{4}-\d{1,2}-\d{1,2}", 3),  # 日期
        (r"\d{4}年\d{1,2}月\d{1,2}日", 3),
        (r"电话|手机号|微信|QQ|联系方式", 3),
        (r"地址|地点|位置", 2),
        (r"时间|日期|有效期|截止", 2),
        (r"今天|明天|后天|昨天|前天", 3),  # 相对时间（提高权重）
        (r"下周|下月|下年|上周|上月|上年|本周|本月|本年", 3),
        (r"今年|去年|明年", 3),
        # ---- 情绪内容（所有场景通用，用于情绪提取） ----
        (r"开心|高兴|快乐|兴奋|愉快|幸福|满足|喜悦|欣喜", 4),
        (r"难过|悲伤|伤心|痛苦|委屈|失落|沮丧|郁闷|忧郁|绝望", 4),
        (r"生气|愤怒|烦躁|焦虑|紧张|害怕|恐惧|担心|担忧|压力", 4),
        (r"感动|温暖|感激|感谢|惊喜|期待|憧憬|向往", 3),
        (r"无聊|无奈|尴尬|后悔|遗憾|羞愧|惭愧", 3),
        (r"好[开高快]心|很[开高快]心|超[开高快]心|非常[开高快]心", 4),
        (r"好难过|好伤心|好悲伤|好委屈|好痛苦", 4),
        (r"好开心|好高兴|好快乐|好幸福|好感动", 4),
        # ---- 兴趣/爱好内容（所有场景通用，用于兴趣提取） ----
        (r"喜欢|热爱|爱好|兴趣|擅长|享受|沉迷|着迷|痴迷", 4),
        (r"不喜欢|讨厌|厌恶|反感|排斥", 3),
        (r"羽毛球|篮球|足球|排球|乒乓球|网球|棒球|高尔夫", 4),
        (r"游泳|跑步|健身|瑜伽|舞蹈|武术|骑行|登山|徒步", 4),
        (r"音乐|唱歌|吉他|钢琴|绘画|摄影|书法|手工|烹饪", 4),
        (r"游戏|电影|动漫|小说|阅读|旅游|美食|宠物", 3),
    ]
    
    BASE_LOW_PRIORITY = [
        (r"\d{1,2}:\d{2}", 2),  # 时间点 HH:MM
        (r"\d{1,2}点\d{0,2}分?", 2),  # 时间点 X点Y分 或 X点
        (r"上午|下午|中午|晚上|早上|傍晚|凌晨", 2),  # 时段（提高权重并扩充）
        (r"AM|PM|am|pm", 1),
        # ---- 情绪程度副词（辅助情绪识别） ----
        (r"特别|非常|超级|极其|十分|很|好[开高快]|太.*了", 1),
    ]
    
    BASE_FILLERS = {
        # 基础寒暄
        "你好", "您好", "在吗", "在的", "在呢", "嗯", "嗯嗯", "哦", "哦哦",
        "好的", "好", "行", "可以", "不可以", "谢谢", "多谢", "感谢",
        "拜拜", "再见", "88", "拜", "回见",
        # 口头禅
        "哈哈", "呵呵", "哈哈哈", "嘿嘿", "嘻嘻", "hiahia",
        "额", "呃", "啊", "诶", "唉", "哎", "嗯哼",
        # 确认词
        "是的", "对", "对的", "没错", "嗯嗯", "好嘞", "收到", "明白", "了解", "知道了",
        # 标点和符号
        "。。。", "...", "???", "？？？", "!!!", "！！！",
        # 表情符号
        "[微笑]", "[呲牙]", "[发呆]", "[得意]", "[流泪]", "[害羞]", "[闭嘴]",
        "[睡]", "[大哭]", "[尴尬]", "[发怒]", "[调皮]", "[龇牙]", "[惊讶]",
        "[难过]", "[酷]", "[冷汗]", "[抓狂]", "[吐]", "[偷笑]", "[可爱]",
        "[白眼]", "[傲慢]", "[饥饿]", "[困]", "[惊恐]", "[流汗]", "[憨笑]",
        # 网络用语
        "hhh", "hhhh", "2333", "666", "gg", "ok", "OK", "okok",
        "emmm", "emm", "em", "mmp", "wtf", "omg",
    }
    
    BASE_QUESTION_KEYWORDS = {
        "什么", "为什么", "怎么", "如何", "哪里", "哪个", "谁", "多少", "几点", "何时", "吗"
    }
    
    BASE_DECISION_KEYWORDS = {
        "必须", "一定", "务必", "需要", "要求", "规定", "应该",
        "承诺", "保证", "确保", "负责", "同意", "答应"
    }
    
    @classmethod
    def get_education_config(cls) -> ScenePatterns:
        """教育场景配置"""
        return ScenePatterns(
            high_priority_patterns=cls.BASE_HIGH_PRIORITY + [
                # 成绩相关（最高优先级）
                (r"成绩|分数|得分|满分|及格|不及格", 6),
                (r"GPA|绩点|学分|平均分", 6),
                (r"\d+分|\d+\.?\d*分", 5),  # 具体分数
                (r"排名|名次|第.{1,3}名", 5),  # 支持"第三名"、"第1名"等
                
                # 学籍信息
                (r"学号|学生证|教师工号|工号", 5),
                (r"班级|年级|专业|院系", 4),
                
                # 课程相关
                (r"课程|科目|学科|必修|选修", 4),
                (r"教材|课本|教科书|参考书", 4),
                (r"章节|第.{1,3}章|第.{1,3}节", 3),  # 支持"第三章"、"第1章"等
                
                # 学科内容（新增）
                (r"微积分|导数|积分|函数|极限|微分", 4),
                (r"代数|几何|三角|概率|统计", 4),
                (r"物理|化学|生物|历史|地理", 4),
                (r"英语|语文|数学|政治|哲学", 4),
                (r"定义|定理|公式|概念|原理|法则", 3),
                (r"例题|解题|证明|推导|计算", 3),
            ],
            medium_priority_patterns=cls.BASE_MEDIUM_PRIORITY + [
                # 教学活动
                (r"作业|练习|习题|题目", 3),
                (r"考试|测验|测试|考核|期中|期末", 3),
                (r"上课|下课|课堂|讲课", 2),
                (r"提问|回答|发言|讨论", 2),
                (r"问一下|请教|咨询|询问", 2),  # 新增：问询相关
                (r"理解|明白|懂|掌握|学会", 2),  # 新增：学习状态
                
                # 时间安排
                (r"课表|课程表|时间表", 3),
                (r"第.{1,3}节课|第.{1,3}周", 2),  # 支持"第三节课"、"第1周"等
            ],
            low_priority_patterns=cls.BASE_LOW_PRIORITY + [
                (r"老师|教师|同学|学生", 1),
                (r"教室|实验室|图书馆", 1),
            ],
            filler_phrases=cls.BASE_FILLERS | {
                # 教育场景特有填充词（移除了"明白了"、"懂了"、"不懂"等，这些在教育场景中有意义）
                "老师好", "同学们好", "上课", "下课", "起立", "坐下",
                "举手", "请坐", "很好", "不错", "继续",
                "下一个", "下一题", "下一位", "还有吗", "还有问题吗",
            },
            question_keywords=cls.BASE_QUESTION_KEYWORDS | {
                "为啥", "咋", "咋办", "怎样", "如何做",
                "能不能", "可不可以", "行不行", "对不对", "是不是",
            },
            decision_keywords=cls.BASE_DECISION_KEYWORDS | {
                "必考", "重点", "考点", "难点", "关键",
                "记住", "背诵", "掌握", "理解", "复习",
            }
        )
    
    @classmethod
    def get_online_service_config(cls) -> ScenePatterns:
        """在线服务场景配置"""
        return ScenePatterns(
            high_priority_patterns=cls.BASE_HIGH_PRIORITY + [
                # 工单相关（最高优先级）
                (r"工单号|工单编号|ticket|TK\d+", 6),
                (r"工单状态|处理中|已解决|已关闭|待处理", 5),
                (r"优先级|紧急|高优先级|P0|P1|P2", 5),
                
                # 产品信息
                (r"产品型号|型号|SKU|产品编号", 5),
                (r"序列号|SN|设备号", 5),
                (r"版本号|软件版本|固件版本", 4),
                
                # 问题描述
                (r"故障|错误|异常|bug|问题", 4),
                (r"错误代码|故障代码|error code", 5),
                (r"无法|不能|失败|报错", 3),
            ],
            medium_priority_patterns=cls.BASE_MEDIUM_PRIORITY + [
                # 服务相关
                (r"退款|退货|换货|补发", 4),
                (r"发票|收据|凭证", 3),
                (r"物流|快递|运单号", 3),
                (r"保修|质保|售后", 3),
                
                # 时效相关
                (r"SLA|响应时间|处理时长", 4),
                (r"超时|延迟|等待", 2),
            ],
            low_priority_patterns=cls.BASE_LOW_PRIORITY + [
                (r"客服|工程师|技术支持", 1),
                (r"用户|客户|会员", 1),
            ],
            filler_phrases=cls.BASE_FILLERS | {
                # 在线服务特有填充词
                "您好", "请问", "请稍等", "稍等", "马上", "立即",
                "正在查询", "正在处理", "正在为您", "帮您查一下",
                "还有其他问题吗", "还需要什么帮助", "很高兴为您服务",
                "感谢您的耐心等待", "抱歉让您久等了",
                "已记录", "已反馈", "已转接", "已升级",
                "祝您生活愉快", "再见", "欢迎下次咨询",
            },
            question_keywords=cls.BASE_QUESTION_KEYWORDS | {
                "能否", "可否", "是否", "有没有", "能不能",
                "怎么办", "如何处理", "怎么解决",
            },
            decision_keywords=cls.BASE_DECISION_KEYWORDS | {
                "立即处理", "马上解决", "尽快", "优先",
                "升级", "转接", "派单", "跟进",
                "补偿", "赔偿", "退款", "换货",
            }
        )
    
    @classmethod
    def get_outbound_config(cls) -> ScenePatterns:
        """外呼场景配置"""
        return ScenePatterns(
            high_priority_patterns=cls.BASE_HIGH_PRIORITY + [
                # 意向相关（最高优先级）
                (r"意向|意愿|兴趣|感兴趣", 6),
                (r"A类|B类|C类|D类|高意向|低意向", 6),
                (r"成交|签约|下单|购买|确认", 6),
                
                # 联系信息（外呼场景中更重要）
                (r"预约|约定|安排|确定时间", 5),
                (r"下次联系|回访|跟进", 5),
                (r"方便|有空|可以|时间", 4),
                
                # 通话状态
                (r"接通|未接通|占线|关机|停机", 4),
                (r"通话时长|通话时间", 3),
            ],
            medium_priority_patterns=cls.BASE_MEDIUM_PRIORITY + [
                # 客户信息
                (r"姓名|称呼|先生|女士", 3),
                (r"公司|单位|职位|职务", 3),
                (r"需求|要求|期望", 3),
                
                # 跟进状态
                (r"跟进状态|进展|进度", 3),
                (r"已联系|待联系|联系中", 2),
                (r"拒绝|不感兴趣|考虑|再说", 3),
            ],
            low_priority_patterns=cls.BASE_LOW_PRIORITY + [
                (r"销售|客户经理|业务员", 1),
                (r"产品|服务|方案", 1),
            ],
            filler_phrases=cls.BASE_FILLERS | {
                # 外呼场景特有填充词
                "您好", "喂", "hello", "打扰了", "不好意思",
                "方便接电话吗", "现在方便吗", "占用您一点时间",
                "我是", "我们是", "我们公司", "我们这边",
                "了解一下", "介绍一下", "简单说一下",
                "考虑考虑", "想一想", "再说", "再看看",
                "不需要", "不感兴趣", "没兴趣", "不用了",
                "好的", "行", "可以", "没问题", "那就这样",
                "再联系", "回头聊", "有需要再说",
            },
            question_keywords=cls.BASE_QUESTION_KEYWORDS | {
                "有没有", "需不需要", "要不要", "考虑不考虑",
                "了解吗", "知道吗", "听说过吗",
                "方便吗", "有空吗", "在吗",
            },
            decision_keywords=cls.BASE_DECISION_KEYWORDS | {
                "确定", "决定", "选择", "购买", "下单",
                "预约", "安排", "约定", "确认",
                "跟进", "回访", "联系", "沟通",
            }
        )
    
    @classmethod
    def get_config(cls, scene: str, fallback_to_generic: bool = True) -> ScenePatterns:
        """根据场景名称获取配置
        
        Args:
            scene: 场景名称 ('education', 'online_service', 'outbound' 或其他)
            fallback_to_generic: 如果场景不存在，是否降级到通用配置
            
        Returns:
            对应场景的配置，如果场景不存在：
            - fallback_to_generic=True: 返回通用配置（仅基础规则）
            - fallback_to_generic=False: 抛出异常
        """
        scene_map = {
            'education': cls.get_education_config,
            'online_service': cls.get_online_service_config,
            'outbound': cls.get_outbound_config,
        }
        
        if scene in scene_map:
            return scene_map[scene]()
        
        if fallback_to_generic:
            # 返回通用配置（仅包含基础规则，不包含场景特定规则）
            return cls.get_generic_config()
        else:
            raise ValueError(f"不支持的场景: {scene}，支持的场景: {list(scene_map.keys())}")
    
    @classmethod
    def get_generic_config(cls) -> ScenePatterns:
        """通用场景配置 - 仅包含基础规则，适用于未定义的场景
        
        这是一个保守的配置，只使用最通用的规则，避免误删重要信息
        """
        return ScenePatterns(
            high_priority_patterns=cls.BASE_HIGH_PRIORITY,
            medium_priority_patterns=cls.BASE_MEDIUM_PRIORITY,
            low_priority_patterns=cls.BASE_LOW_PRIORITY,
            filler_phrases=cls.BASE_FILLERS,
            question_keywords=cls.BASE_QUESTION_KEYWORDS,
            decision_keywords=cls.BASE_DECISION_KEYWORDS
        )
    
    @classmethod
    def get_all_scenes(cls) -> List[str]:
        """获取所有预定义场景的列表"""
        return ['education', 'online_service', 'outbound']
    
    @classmethod
    def is_scene_supported(cls, scene: str) -> bool:
        """检查场景是否有专门的配置支持
        
        Args:
            scene: 场景名称
            
        Returns:
            True: 有专门配置
            False: 将使用通用配置
        """
        return scene in cls.get_all_scenes()
