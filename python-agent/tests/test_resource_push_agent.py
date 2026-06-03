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


def test_resource_push_agent_binds_generated_assets_to_learning_path_steps() -> None:
    agent = ResourcePushAgent()

    plan = agent._build_path_bound_resource_plan(
        learning_path={
            "steps": [
                {
                    "stepId": "step-1",
                    "title": "理解最左匹配",
                    "objective": "掌握联合索引最左匹配规则",
                    "targetKnowledgePoints": ["最左匹配"],
                    "preferredResourceTypes": ["DOCUMENT", "VIDEO"],
                }
            ]
        },
        params={
            "generatedAssets": [
                {
                    "assetType": "DOCUMENT",
                    "title": "最左匹配导学文档",
                    "summary": "讲解最左匹配规则",
                    "generatedBy": "LLM",
                    "contentOrigin": "LLM",
                    "provider": "test",
                    "model": "test-model",
                    "agentName": "document_generation",
                    "evidenceIds": ["doc-1"],
                    "fallback": False,
                    "fromCache": False,
                }
            ]
        },
        profile_context={"preferredResourceTypes": ["DOCUMENT"], "primaryWeakPoint": "最左匹配"},
    )

    step_plan = plan["stepResources"][0]
    assert step_plan["stepId"] == "step-1"
    assert step_plan["resources"][0]["source"] == "generated"
    assert step_plan["resources"][0]["fallback"] is False
    assert plan["coverageGaps"][0]["missingResourceTypes"] == ["VIDEO"]


def test_resource_push_agent_normalizes_resource_type_aliases_for_path_binding() -> None:
    agent = ResourcePushAgent()

    plan = agent._build_path_bound_resource_plan(
        learning_path={
            "steps": [
                {
                    "stepId": "step-1",
                    "title": "CODE_CASE alias",
                    "targetKnowledgePoints": ["alias"],
                    "preferredResourceTypes": ["EXPLANATION", "CODE_CASE", "PRACTICAL_CASE"],
                }
            ]
        },
        params={
            "generatedAssets": [
                {"assetType": "DOCUMENT", "title": "alias document", "summary": "alias"},
                {"assetType": "CODE", "title": "alias code", "summary": "alias"},
            ]
        },
        profile_context={},
    )

    resources = plan["stepResources"][0]["resources"]
    assert [item["resourceType"] for item in resources] == ["DOCUMENT", "CODE"]
    assert plan["coverageGaps"] == []


def test_resource_push_agent_prefers_profile_analysis_context() -> None:
    agent = ResourcePushAgent()

    context = agent._extract_profile_context(
        {
            "profile": {
                "weakPoints": ["old-profile"],
                "preferredResourceTypes": ["READING"],
            },
            "profileAnalysis": {
                "weakPoints": ["new-profile-analysis"],
                "preferredResourceTypes": ["CODE_CASE"],
            },
            "learningContext": {"course": "course", "chapter": "chapter"},
        },
        snapshot=type(
            "Snapshot",
            (),
            {
                "preferred_style": "step_by_step",
                "student_level": "BASIC",
                "knowledge_gaps": [],
                "current_course": "snapshot-course",
                "current_chapter": "snapshot-chapter",
            },
        )(),
    )

    assert context["primaryWeakPoint"] == "new-profile-analysis"
    assert context["preferredResourceTypes"] == ["CODE"]


def test_resource_push_agent_ignores_empty_profile_analysis_context() -> None:
    agent = ResourcePushAgent()

    context = agent._extract_profile_context(
        {
            "profile": {
                "weakPoints": ["old-profile"],
                "preferredResourceTypes": ["READING"],
            },
            "profileAnalysis": {
                "weakPoints": [],
                "preferredResourceTypes": [],
            },
            "learningContext": {"course": "course", "chapter": "chapter"},
        },
        snapshot=type(
            "Snapshot",
            (),
            {
                "preferred_style": "",
                "student_level": "BASIC",
                "knowledge_gaps": [],
                "current_course": "snapshot-course",
                "current_chapter": "snapshot-chapter",
            },
        )(),
    )

    assert context["primaryWeakPoint"] == "old-profile"
    assert context["preferredResourceTypes"] == ["READING"]


def test_resource_push_agent_uses_retrieval_evidence_without_faking_download_url() -> None:
    agent = ResourcePushAgent()

    plan = agent._build_path_bound_resource_plan(
        learning_path={
            "steps": [
                {
                    "stepId": "step-1",
                    "title": "理解最左匹配",
                    "targetKnowledgePoints": ["最左匹配"],
                    "preferredResourceTypes": ["READING"],
                }
            ]
        },
        params={
            "retrievalEvidence": [
                {
                    "title": "联合索引知识点",
                    "slug": "db-index",
                    "channel": "vector",
                    "evidence": "最左匹配是联合索引的重要规则",
                    "url": None,
                }
            ]
        },
        profile_context={},
    )

    resource = plan["stepResources"][0]["resources"][0]
    assert resource["source"] == "retrieval_evidence"
    assert resource["downloadUrl"] is None
