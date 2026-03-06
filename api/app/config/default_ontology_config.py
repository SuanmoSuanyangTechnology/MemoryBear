"""默认本体场景配置

本模块定义系统预设的本体场景和实体类型配置。
这些配置用于在工作空间创建时自动初始化默认场景。
支持中英文双语配置，根据用户语言偏好创建对应语言的场景。
"""

# 在线教育场景配置
ONLINE_EDUCATION_SCENE = {
    "name_chinese": "在线教育",
    "name_english": "Online Education",
    "description_chinese": "适用于在线教育平台的本体建模，包含学生、教师、课程等核心实体类型",
    "description_english": "Ontology modeling for online education platforms, including core entity types such as students, teachers, and courses",
    "types": [
        {
            "name_chinese": "学生",
            "name_english": "Student",
            "description_chinese": "在教育系统中接受教育的个体，包含姓名、学号、年级、班级等属性",
            "description_english": "Individuals receiving education in the education system, including attributes such as name, student ID, grade, and class"
        },
        {
            "name_chinese": "教师",
            "name_english": "Teacher",
            "description_chinese": "在教育系统中提供教学服务的个体，包含姓名、工号、任教学科、职称等属性",
            "description_english": "Individuals providing teaching services in the education system, including attributes such as name, employee ID, teaching subject, and title"
        },
        {
            "name_chinese": "课程",
            "name_english": "Course",
            "description_chinese": "教育系统中的教学内容单元，包含课程名称、课程代码、学分、学时等属性",
            "description_english": "Teaching content units in the education system, including attributes such as course name, course code, credits, and class hours"
        },
        {
            "name_chinese": "作业",
            "name_english": "Assignment",
            "description_chinese": "课程中布置的学习任务，包含作业标题、截止日期、所属课程、提交状态等属性",
            "description_english": "Learning tasks assigned in courses, including attributes such as assignment title, deadline, course, and submission status"
        },
        {
            "name_chinese": "成绩",
            "name_english": "Grade",
            "description_chinese": "学生学习成果的评价结果，包含分数、评级、考试类型、所属课程等属性",
            "description_english": "Evaluation results of student learning outcomes, including attributes such as score, rating, exam type, and course"
        },
        {
            "name_chinese": "考试",
            "name_english": "Exam",
            "description_chinese": "评估学生学习成果的测试活动，包含考试名称、时间、地点、科目等属性",
            "description_english": "Test activities to assess student learning outcomes, including attributes such as exam name, time, location, and subject"
        },
        {
            "name_chinese": "教室",
            "name_english": "Classroom",
            "description_chinese": "进行教学活动的物理或虚拟空间，包含教室编号、容量、设备等属性",
            "description_english": "Physical or virtual spaces for teaching activities, including attributes such as classroom number, capacity, and equipment"
        },
        {
            "name_chinese": "学科",
            "name_english": "Subject",
            "description_chinese": "知识的分类领域，包含学科名称、代码、所属院系等属性",
            "description_english": "Classification domains of knowledge, including attributes such as subject name, code, and department"
        },
        {
            "name_chinese": "教材",
            "name_english": "Textbook",
            "description_chinese": "教学使用的书籍或资料，包含书名、作者、出版社、ISBN等属性",
            "description_english": "Books or materials used for teaching, including attributes such as title, author, publisher, and ISBN"
        },
        {
            "name_chinese": "班级",
            "name_english": "Class",
            "description_chinese": "学生的组织单位，包含班级名称、年级、人数、班主任等属性",
            "description_english": "Organizational units of students, including attributes such as class name, grade, number of students, and class teacher"
        },
        {
            "name_chinese": "学期",
            "name_english": "Semester",
            "description_chinese": "教学时间的划分单位，包含学期名称、开始时间、结束时间等属性",
            "description_english": "Time division units for teaching, including attributes such as semester name, start time, and end time"
        },
        {
            "name_chinese": "课时",
            "name_english": "Class Hour",
            "description_chinese": "课程的时间单位，包含上课时间、地点、教师、课程等属性",
            "description_english": "Time units of courses, including attributes such as class time, location, teacher, and course"
        },
        {
            "name_chinese": "教学计划",
            "name_english": "Teaching Plan",
            "description_chinese": "课程的教学安排，包含教学目标、内容安排、进度计划等属性",
            "description_english": "Teaching arrangements for courses, including attributes such as teaching objectives, content arrangement, and progress plan"
        }
    ]
}

# 情感陪伴场景配置
EMOTIONAL_COMPANION_SCENE = {
    "name_chinese": "情感陪伴",
    "name_english": "Emotional Companion",
    "description_chinese": "适用于情感陪伴应用的本体建模，包含用户、情绪、活动等核心实体类型",
    "description_english": "Ontology modeling for emotional companion applications, including core entity types such as users, emotions, and activities",
    "types": [
        {
            "name_chinese": "用户",
            "name_english": "User",
            "description_chinese": "使用情感陪伴服务的个体，包含姓名、昵称、性格特征、偏好等属性",
            "description_english": "Individuals using emotional companion services, including attributes such as name, nickname, personality traits, and preferences"
        },
        {
            "name_chinese": "情绪",
            "name_english": "Emotion",
            "description_chinese": "用户的情感状态，包含情绪类型、强度、触发原因、持续时间等属性",
            "description_english": "Emotional states of users, including attributes such as emotion type, intensity, trigger cause, and duration"
        },
        {
            "name_chinese": "活动",
            "name_english": "Activity",
            "description_chinese": "用户参与的各类活动，包含活动名称、类型、参与者、时间地点等属性",
            "description_english": "Various activities users participate in, including attributes such as activity name, type, participants, time, and location"
        },
        {
            "name_chinese": "对话",
            "name_english": "Conversation",
            "description_chinese": "用户之间的交流记录，包含对话主题、参与者、时间、关键内容等属性",
            "description_english": "Communication records between users, including attributes such as conversation topic, participants, time, and key content"
        },
        {
            "name_chinese": "兴趣爱好",
            "name_english": "Hobby",
            "description_chinese": "用户的兴趣和爱好，包含爱好名称、类别、熟练程度、相关活动等属性",
            "description_english": "User interests and hobbies, including attributes such as hobby name, category, proficiency level, and related activities"
        },
        {
            "name_chinese": "日常事件",
            "name_english": "Daily Event",
            "description_chinese": "用户日常生活中的事件，包含事件描述、时间、地点、相关人物等属性",
            "description_english": "Events in users' daily lives, including attributes such as event description, time, location, and related people"
        },
        {
            "name_chinese": "关系",
            "name_english": "Relationship",
            "description_chinese": "用户之间的社会关系，包含关系类型、亲密度、建立时间等属性",
            "description_english": "Social relationships between users, including attributes such as relationship type, intimacy, and establishment time"
        },
        {
            "name_chinese": "回忆",
            "name_english": "Memory",
            "description_chinese": "用户的重要记忆片段，包含回忆内容、时间、地点、相关人物等属性",
            "description_english": "Important memory fragments of users, including attributes such as memory content, time, location, and related people"
        },
        {
            "name_chinese": "地点",
            "name_english": "Location",
            "description_chinese": "用户活动的地理位置，包含地点名称、地址、类型、相关事件等属性",
            "description_english": "Geographic locations of user activities, including attributes such as location name, address, type, and related events"
        },
        {
            "name_chinese": "时间节点",
            "name_english": "Time Point",
            "description_chinese": "重要的时间标记，包含日期、事件、意义等属性",
            "description_english": "Important time markers, including attributes such as date, event, and significance"
        },
        {
            "name_chinese": "目标",
            "name_english": "Goal",
            "description_chinese": "用户设定的目标，包含目标描述、截止时间、完成状态、相关活动等属性",
            "description_english": "Goals set by users, including attributes such as goal description, deadline, completion status, and related activities"
        },
        {
            "name_chinese": "成就",
            "name_english": "Achievement",
            "description_chinese": "用户获得的成就，包含成就名称、获得时间、描述、相关目标等属性",
            "description_english": "Achievements obtained by users, including attributes such as achievement name, acquisition time, description, and related goals"
        }
    ]
}

# 导出默认场景列表
DEFAULT_SCENES = [ONLINE_EDUCATION_SCENE, EMOTIONAL_COMPANION_SCENE]


def get_scene_name(scene_config: dict, language: str = "zh") -> str:
    """获取场景名称（根据语言）
    
    Args:
        scene_config: 场景配置字典
        language: 语言类型 ("zh" 或 "en")
        
    Returns:
        对应语言的场景名称
    """
    if language == "en":
        return scene_config.get("name_english", scene_config.get("name_chinese"))
    return scene_config.get("name_chinese")


def get_scene_description(scene_config: dict, language: str = "zh") -> str:
    """获取场景描述（根据语言）
    
    Args:
        scene_config: 场景配置字典
        language: 语言类型 ("zh" 或 "en")
        
    Returns:
        对应语言的场景描述
    """
    if language == "en":
        return scene_config.get("description_english", scene_config.get("description_chinese"))
    return scene_config.get("description_chinese")


def get_type_name(type_config: dict, language: str = "zh") -> str:
    """获取类型名称（根据语言）
    
    Args:
        type_config: 类型配置字典
        language: 语言类型 ("zh" 或 "en")
        
    Returns:
        对应语言的类型名称
    """
    if language == "en":
        return type_config.get("name_english", type_config.get("name_chinese"))
    return type_config.get("name_chinese")


def get_type_description(type_config: dict, language: str = "zh") -> str:
    """获取类型描述（根据语言）
    
    Args:
        type_config: 类型配置字典
        language: 语言类型 ("zh" 或 "en")
        
    Returns:
        对应语言的类型描述
    """
    if language == "en":
        return type_config.get("description_english", type_config.get("description_chinese"))
    return type_config.get("description_chinese")
