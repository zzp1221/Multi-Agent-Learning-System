from src.ai_modules.agents.resource_push_agent import ResourcePushAgent


def test_resource_push_agent_only_accepts_http_direct_urls() -> None:
    agent = ResourcePushAgent()

    assert agent._is_http_url("https://example.com/file.md")
    assert not agent._is_http_url("custom://resource-bucket/resources/thread-pool-guide.md")


def test_resource_push_agent_builds_query_from_profile_context() -> None:
    agent = ResourcePushAgent()

    query = agent._build_query(
        {"resourceType": "CODE_CASE"},
        {
            "primaryWeakPoint": "线程池参数调优",
            "currentCourse": "Java 程序设计",
            "currentChapter": "并发编程",
            "studentLevel": "INTERMEDIATE",
        },
    )

    assert query == "线程池参数调优 / Java 程序设计 / 并发编程 / INTERMEDIATE / 代码案例"


def test_resource_push_agent_filters_non_video_pages() -> None:
    agent = ResourcePushAgent()

    assert not agent._is_valid_video_result({}, "https://example.com/paper.pdf", "并发编程论文")
    assert not agent._is_valid_video_result({}, "https://github.com/example/repo", "示例仓库")
    assert agent._is_valid_video_result({}, "https://www.bilibili.com/video/BV1xx", "并发编程视频讲解")


def test_resource_push_agent_scores_topic_relevance_for_red_black_tree() -> None:
    agent = ResourcePushAgent()
    profile_context = {
        "primaryWeakPoint": "红黑树旋转与染色",
        "currentCourse": "数据结构",
        "currentChapter": "红黑树",
        "learningGoal": "理解红黑树插入修复",
        "weakPoints": ["红黑树旋转与染色"],
    }

    relevant_score = agent._score_topic_relevance(
        title="红黑树插入删除可视化讲解",
        summary="结合旋转与染色过程理解平衡维护",
        url="https://example.com/red-black-tree",
        profile_context=profile_context,
    )
    unrelated_score = agent._score_topic_relevance(
        title="OpenAI Dify 应用搭建实战",
        summary="Python 和 JavaScript 调用大模型平台",
        url="https://example.com/dify-openai",
        profile_context=profile_context,
    )

    assert relevant_score >= 4
    assert unrelated_score < 4


def test_resource_push_agent_rejects_generic_unrelated_resources() -> None:
    agent = ResourcePushAgent()
    profile_context = {
        "primaryWeakPoint": "红黑树",
        "currentCourse": "数据结构",
        "currentChapter": "红黑树",
        "learningGoal": "掌握红黑树调整",
        "weakPoints": ["红黑树"],
    }

    bad_titles = [
        "JavaScript 前端入门",
        "Python 自动化办公",
        "OpenAI API 调用教程",
        "Dify 工作流搭建",
        "广播电视行业白皮书 PDF",
    ]

    assert all(
        agent._score_topic_relevance(
            title=title,
            summary="高质量学习资源与案例",
            url="https://example.com/resource",
            profile_context=profile_context,
        ) < 4
        for title in bad_titles
    )
