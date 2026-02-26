"""默认本体场景配置

本模块定义系统预设的本体场景和实体类型配置。
这些配置用于在工作空间创建时自动初始化默认场景。
"""

# 在线教育场景配置
ONLINE_EDUCATION_SCENE = {
    "name_chinese": "在线教育",
    "name_english": "Online Education",
    "description": "适用于在线教育平台的本体建模，包含学生、教师、课程等核心实体类型",
    "types": [
        {
            "name_chinese": "学生",
            "name_english": "Student",
            "description": "在教育系统中接受教育的个体，包含姓名、学号、年级、班级等属性"
        },
        {
            "name_chinese": "教师",
            "name_english": "Teacher",
            "description": "在教育系统中提供教学服务的个体，包含姓名、工号、任教学科、职称等属性"
        },
        {
            "name_chinese": "课程",
            "name_english": "Course",
            "description": "教育系统中的教学内容单元，包含课程名称、课程代码、学分、学时等属性"
        },
        {
            "name_chinese": "作业",
            "name_english": "Assignment",
            "description": "课程中布置的学习任务，包含作业标题、截止日期、所属课程、提交状态等属性"
        },
        {
            "name_chinese": "成绩",
            "name_english": "Grade",
            "description": "学生学习成果的评价结果，包含分数、评级、考试类型、所属课程等属性"
        },
        {
            "name_chinese": "考试",
            "name_english": "Exam",
            "description": "评估学生学习成果的测试活动，包含考试名称、时间、地点、科目等属性"
        },
        {
            "name_chinese": "教室",
            "name_english": "Classroom",
            "description": "进行教学活动的物理或虚拟空间，包含教室编号、容量、设备等属性"
        },
        {
            "name_chinese": "学科",
            "name_english": "Subject",
            "description": "知识的分类领域，包含学科名称、代码、所属院系等属性"
        },
        {
            "name_chinese": "教材",
            "name_english": "Textbook",
            "description": "教学使用的书籍或资料，包含书名、作者、出版社、ISBN等属性"
        },
        {
            "name_chinese": "班级",
            "name_english": "Class",
            "description": "学生的组织单位，包含班级名称、年级、人数、班主任等属性"
        },
        {
            "name_chinese": "学期",
            "name_english": "Semester",
            "description": "教学时间的划分单位，包含学期名称、开始时间、结束时间等属性"
        },
        {
            "name_chinese": "课时",
            "name_english": "Class Hour",
            "description": "课程的时间单位，包含上课时间、地点、教师、课程等属性"
        },
        {
            "name_chinese": "教学计划",
            "name_english": "Teaching Plan",
            "description": "课程的教学安排，包含教学目标、内容安排、进度计划等属性"
        }
    ]
}

# 情感陪伴场景配置
EMOTIONAL_COMPANION_SCENE = {
    "name_chinese": "情感陪伴",
    "name_english": "Emotional Companion",
    "description": "适用于情感陪伴应用的本体建模，包含用户、情绪、活动等核心实体类型",
    "types": [
        {
            "name_chinese": "用户",
            "name_english": "User",
            "description": "使用情感陪伴服务的个体，包含姓名、昵称、性格特征、偏好等属性"
        },
        {
            "name_chinese": "情绪",
            "name_english": "Emotion",
            "description": "用户的情感状态，包含情绪类型、强度、触发原因、持续时间等属性"
        },
        {
            "name_chinese": "活动",
            "name_english": "Activity",
            "description": "用户参与的各类活动，包含活动名称、类型、参与者、时间地点等属性"
        },
        {
            "name_chinese": "对话",
            "name_english": "Conversation",
            "description": "用户之间的交流记录，包含对话主题、参与者、时间、关键内容等属性"
        },
        {
            "name_chinese": "兴趣爱好",
            "name_english": "Hobby",
            "description": "用户的兴趣和爱好，包含爱好名称、类别、熟练程度、相关活动等属性"
        },
        {
            "name_chinese": "日常事件",
            "name_english": "Daily Event",
            "description": "用户日常生活中的事件，包含事件描述、时间、地点、相关人物等属性"
        },
        {
            "name_chinese": "关系",
            "name_english": "Relationship",
            "description": "用户之间的社会关系，包含关系类型、亲密度、建立时间等属性"
        },
        {
            "name_chinese": "回忆",
            "name_english": "Memory",
            "description": "用户的重要记忆片段，包含回忆内容、时间、地点、相关人物等属性"
        },
        {
            "name_chinese": "地点",
            "name_english": "Location",
            "description": "用户活动的地理位置，包含地点名称、地址、类型、相关事件等属性"
        },
        {
            "name_chinese": "时间节点",
            "name_english": "Time Point",
            "description": "重要的时间标记，包含日期、事件、意义等属性"
        },
        {
            "name_chinese": "目标",
            "name_english": "Goal",
            "description": "用户设定的目标，包含目标描述、截止时间、完成状态、相关活动等属性"
        },
        {
            "name_chinese": "成就",
            "name_english": "Achievement",
            "description": "用户获得的成就，包含成就名称、获得时间、描述、相关目标等属性"
        }
    ]
}

# 导出默认场景列表
DEFAULT_SCENES = [ONLINE_EDUCATION_SCENE, EMOTIONAL_COMPANION_SCENE]
