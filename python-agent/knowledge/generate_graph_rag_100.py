"""Generate the fixed 100-question GraphRAG-lite evaluation set.

The output keeps the original RAG benchmark contract
(`id`, `expectedTitle`, `expectedSlug`, `expectedTags`, `question`) while adding
graph-specific fields that future graph metrics can consume.

Usage:
  python knowledge/generate_graph_rag_100.py
  python knowledge/generate_graph_rag_100.py --base reports/rag_100_questions.json --output reports/graph_rag_100_questions.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = PROJECT_ROOT / "reports" / "rag_100_questions.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "graph_rag_100_questions.json"
SUITE_VERSION = "graph-rag-lite-eval-v1"


Blueprint = tuple[str, str, list[str], list[str], str]


BLUEPRINTS: list[Blueprint] = [
    ("q001", "CROSS_LAYER_RELATION", ["q022", "q073"], ["POLICY_TO_MECHANISM", "APPLIED_IN"], "把合规策略、操作系统安全机制和 SQL 注入防御串成从治理到落地的安全路径"),
    ("q002", "PREREQUISITE_PATH", ["q043", "q058"], ["RELATED_PROBLEM", "COMPLEXITY_ANALYSIS"], "从 NP 完全性出发，说明图着色这类问题为什么需要归约视角，并补上复杂度分析前置知识"),
    ("q003", "MECHANISM_APPLICATION", ["q060", "q092"], ["IMPLEMENTED_BY", "OPTIMIZED_BY"], "把代码优化、编译器架构和寄存器分配连成后端优化链路"),
    ("q004", "CROSS_LAYER_RELATION", ["q014", "q001"], ["AUTHENTICATION_RELATED", "POLICY_GOVERNED"], "说明网络准入控制与身份认证、合规策略之间的层次关系"),
    ("q005", "PREREQUISITE_PATH", ["q081", "q055"], ["PREREQUISITE_OF", "ALGEBRAIC_FOUNDATION"], "从同余和模运算过渡到 RSA，再联系环与域中的代数结构"),
    ("q006", "MECHANISM_APPLICATION", ["q077", "q093"], ["FOUNDATION_OF", "QUERY_MECHANISM"], "把关系模型、SELECT 查询和触发器串成数据库逻辑层知识链"),
    ("q007", "PREREQUISITE_PATH", ["q094", "q037"], ["BUILDS_ON", "AUTOMATA_PIPELINE"], "说明 NFA、词法分析与 DFA 最小化之间的自动机转换路径"),
    ("q008", "CROSS_LAYER_RELATION", ["q088", "q062"], ["MEMORY_MODEL_RELATED", "ABI_RELATED"], "把内存对齐、指针算术和 ABI/链接器规则放在同一条底层内存路径里解释"),
    ("q009", "CROSS_LAYER_RELATION", ["q091", "q054"], ["SECURITY_FOUNDATION", "INFORMATION_THEORY"], "说明信息论中的熵和认证码、区块链密码学之间的安全基础关系"),
    ("q010", "MECHANISM_APPLICATION", ["q028", "q070"], ["RENDERING_TECHNIQUE", "VISUAL_EFFECT"], "把环境映射、PBR 材质和光照模型放进图形渲染效果链路"),
    ("q011", "MULTI_HOP_RELATION", ["q043", "q052"], ["COMBINATORICS_RELATED", "GRAPH_THEORY_RELATED"], "从组合计数思想连接图着色、欧拉图与哈密顿图"),
    ("q012", "COMPARISON", ["q041", "q056"], ["ALGORITHM_ANALOGY", "OPTIMIZATION_PROBLEM"], "比较磁盘调度和最短路径算法都在怎样处理局部选择与全局代价"),
    ("q013", "COMPARISON", ["q057", "q069"], ["LANGUAGE_FEATURE", "TYPE_MODEL_RELATED"], "把 Python 数据类、类型系统和 Go 语言特性放在程序抽象能力里比较"),
    ("q014", "CROSS_LAYER_RELATION", ["q076", "q091"], ["PROTOCOL_STACK", "AUTHENTICATION_RELATED"], "说明 OAuth2/OIDC 如何建立在 HTTP 交互与消息认证思想之上"),
    ("q015", "PREREQUISITE_PATH", ["q035", "q053"], ["SYNCHRONIZATION_FOUNDATION", "ABSTRACTION_OF"], "从 Peterson 算法过渡到生产者消费者和管程，解释同步机制抽象升级"),
    ("q016", "MECHANISM_APPLICATION", ["q051", "q032"], ["CONSENSUS_RELATED", "IMPLEMENTED_BY"], "说明 ZooKeeper 与 ZAB、Multi-Paxos 在一致性和日志复制上的关系"),
    ("q017", "PREREQUISITE_PATH", ["q066", "q055"], ["ALGEBRAIC_FOUNDATION", "SPECIAL_CASE_OF"], "把置换群放到群、环与域的代数结构谱系中定位"),
    ("q018", "COMMON_MISTAKE", ["q022", "q001"], ["THREAT_TO_MECHANISM", "POLICY_RESPONSE"], "从恶意软件防护追溯到操作系统安全机制和安全策略，区分威胁、机制和制度"),
    ("q019", "CROSS_LAYER_RELATION", ["q057", "q079"], ["TYPE_SAFETY_RELATED", "LANGUAGE_FEATURE"], "说明 Rust 智能指针、类型系统和迭代器组合器如何共同服务内存安全和抽象"),
    ("q020", "PREREQUISITE_PATH", ["q085", "q096"], ["PARSER_FOUNDATION", "PARSER_COMPARISON"], "把 First/Follow 集与递归下降、LR 分析连接成语法分析学习路径"),
    ("q021", "COMPARISON", ["q070", "q028"], ["RENDERING_TECHNIQUE", "MATERIAL_MODEL"], "比较着色频率、光照模型和 PBR 在渲染管线中的不同位置"),
    ("q022", "CROSS_LAYER_RELATION", ["q001", "q073"], ["SECURITY_MECHANISM", "DATA_SECURITY"], "从操作系统安全机制连接到等保策略和数据库注入防御"),
    ("q023", "CROSS_LAYER_RELATION", ["q048", "q050"], ["MEMORY_SYSTEM", "PERFORMANCE_OPTIMIZATION"], "说明写时复制与页表/TLB、零拷贝之间共享的内存映射思想"),
    ("q024", "PREREQUISITE_PATH", ["q040", "q066"], ["ALGEBRAIC_FOUNDATION", "GENERALIZES_TO"], "从半群到独异点再到群，建立代数结构的层级路径"),
    ("q025", "CROSS_LAYER_RELATION", ["q080", "q045"], ["DEPLOYMENT_ARCHITECTURE", "IOT_PROTOCOL"], "把边缘计算、物联网协议和云原生架构放到端云协同链路中解释"),
    ("q026", "MECHANISM_APPLICATION", ["q061", "q039"], ["SCHEDULING_RELATED", "GRAPH_ALGORITHM_RELATED"], "说明关键路径、活动选择和最小生成树在图模型与优化目标上的联系和差异"),
    ("q027", "MECHANISM_APPLICATION", ["q044", "q015"], ["CONCURRENCY_MECHANISM", "SYNCHRONIZATION_RELATED"], "从 pthread 线程创建连接到线程池复用和 Peterson 互斥思想"),
    ("q028", "CROSS_LAYER_RELATION", ["q070", "q010"], ["MATERIAL_MODEL", "LIGHTING_RELATED"], "说明 PBR 材质、光照模型和环境映射如何共同决定真实感渲染"),
    ("q029", "COMPARISON", ["q030", "q013"], ["LANGUAGE_FEATURE", "RESOURCE_MANAGEMENT"], "比较上下文管理器、生成器协程和数据类在 Python 抽象中的分工"),
    ("q030", "CROSS_LAYER_RELATION", ["q089", "q029"], ["ASYNC_MODEL_RELATED", "LANGUAGE_RUNTIME"], "把 Python 生成器协程与 JavaScript 微任务队列联系起来解释异步运行时"),
    ("q031", "COMPARISON", ["q081", "q054"], ["CRYPTOGRAPHY_RELATED", "SECURITY_MODEL"], "比较量子密码学、RSA 和区块链密码学在安全假设上的差异"),
    ("q032", "COMPARISON", ["q051", "q016"], ["CONSENSUS_RELATED", "LOG_REPLICATION"], "比较 Multi-Paxos、ZAB 和 ZooKeeper 在共识服务中的职责边界"),
    ("q033", "COMMUNITY_SUMMARY", ["q034", "q045"], ["PROCESS_MODEL", "ENGINEERING_PRACTICE"], "总结敏捷开发、Scrum/Kanban 和云原生交付之间的软件工程实践关系"),
    ("q034", "COMPARISON", ["q033", "q045"], ["PROCESS_VARIANT", "DELIVERY_MODEL"], "比较 Scrum、Kanban 与广义敏捷开发，并说明它们如何影响云原生交付节奏"),
    ("q035", "PREREQUISITE_PATH", ["q053", "q098"], ["SYNCHRONIZATION_PATTERN", "GENERALIZES_TO"], "从生产者消费者问题过渡到管程和读者写者问题，提炼同步模式"),
    ("q036", "COMPARISON", ["q087", "q064"], ["DYNAMIC_PROGRAMMING_RELATED", "SEQUENCE_PROBLEM"], "比较 LCS、编辑距离和归并排序在序列处理中的问题建模差异"),
    ("q037", "PREREQUISITE_PATH", ["q094", "q007"], ["AUTOMATA_PIPELINE", "LEXICAL_ANALYSIS"], "从 NFA 到 DFA，再到 DFA 最小化，说明词法分析的自动机构造路径"),
    ("q038", "COMPARISON", ["q083", "q009"], ["DATA_REPRESENTATION", "INFORMATION_ENCODING"], "比较 BCD/ASCII、IEEE754 和信息论视角下的编码与表示问题"),
    ("q039", "COMPARISON", ["q056", "q041"], ["GRAPH_ALGORITHM_RELATED", "OPTIMIZATION_PROBLEM"], "比较最小生成树、Dijkstra 和 Bellman-Ford 的图优化目标与适用条件"),
    ("q040", "PREREQUISITE_PATH", ["q024", "q066"], ["ALGEBRAIC_FOUNDATION", "STRUCTURE_HIERARCHY"], "说明独异点位于半群和群之间的代数结构层级"),
    ("q041", "COMPARISON", ["q056", "q039"], ["SHORTEST_PATH_RELATED", "GRAPH_ALGORITHM_RELATED"], "比较 Bellman-Ford、Dijkstra 和最小生成树在边权约束和目标函数上的差异"),
    ("q042", "MECHANISM_APPLICATION", ["q068", "q077"], ["CONCURRENCY_CONTROL", "QUERY_VISIBILITY"], "说明 MVCC 如何影响 SQL SELECT 的可见性和并发读写行为"),
    ("q043", "CROSS_LAYER_RELATION", ["q052", "q002"], ["GRAPH_THEORY_RELATED", "COMPLEXITY_RELATED"], "把图着色连接到欧拉/哈密顿图和 NP 完全性，解释图论问题的复杂度谱系"),
    ("q044", "MECHANISM_APPLICATION", ["q027", "q035"], ["CONCURRENCY_REUSE", "SYNCHRONIZATION_RELATED"], "从线程池实现联系 pthread 和生产者消费者队列模型"),
    ("q045", "CROSS_LAYER_RELATION", ["q025", "q016"], ["DEPLOYMENT_ARCHITECTURE", "DISTRIBUTED_COORDINATION"], "说明云原生架构与边缘计算、ZooKeeper 协调服务之间的系统架构关系"),
    ("q046", "PREREQUISITE_PATH", ["q044", "q015"], ["OS_FOUNDATION", "CONCURRENCY_RELATED"], "从进程状态转换过渡到线程池调度和互斥算法，建立并发执行基础路径"),
    ("q047", "CROSS_LAYER_RELATION", ["q075", "q076"], ["NETWORK_LAYER_TO_TRANSPORT", "PROTOCOL_STACK"], "把 IPv4/IPv6、TCP 握手和 HTTP 放进网络协议栈中说明层间关系"),
    ("q048", "MECHANISM_APPLICATION", ["q023", "q088"], ["MEMORY_SYSTEM", "LOW_LEVEL_MEMORY"], "说明页表/TLB 与写时复制、C 指针内存模型之间的映射关系"),
    ("q049", "COMPARISON", ["q086", "q099"], ["STRING_ALGORITHM_RELATED", "INDEX_STRUCTURE"], "比较后缀数组/后缀树、后缀数组算法和 KMP 在字符串检索中的角色"),
    ("q050", "CROSS_LAYER_RELATION", ["q023", "q075"], ["PERFORMANCE_OPTIMIZATION", "NETWORK_IO"], "说明零拷贝如何同时涉及内存映射思想和 TCP 网络 I/O"),
    ("q051", "COMPARISON", ["q016", "q032"], ["CONSENSUS_RELATED", "COORDINATION_SERVICE"], "比较 ZAB、ZooKeeper 和 Multi-Paxos 在日志复制和主从切换中的关系"),
    ("q052", "CROSS_LAYER_RELATION", ["q043", "q039"], ["GRAPH_THEORY_RELATED", "GRAPH_ALGORITHM_RELATED"], "把欧拉图、哈密顿图与图着色、最小生成树放到图论问题分类中比较"),
    ("q053", "MECHANISM_APPLICATION", ["q035", "q098"], ["SYNCHRONIZATION_ABSTRACTION", "CLASSIC_PROBLEM"], "说明管程如何抽象生产者消费者和读者写者这类同步问题"),
    ("q054", "CROSS_LAYER_RELATION", ["q081", "q091"], ["CRYPTOGRAPHY_FOUNDATION", "AUTHENTICATION_RELATED"], "把区块链密码学、RSA 和 HMAC 放到完整性、身份和不可篡改性链路中"),
    ("q055", "PREREQUISITE_PATH", ["q066", "q005"], ["ALGEBRAIC_FOUNDATION", "NUMBER_THEORY_RELATED"], "从群到环与域，再联系同余模运算，建立代数到数论的学习路径"),
    ("q056", "COMPARISON", ["q041", "q039"], ["SHORTEST_PATH_RELATED", "GRAPH_ALGORITHM_RELATED"], "比较 Dijkstra、Bellman-Ford 和最小生成树的前置条件与失败场景"),
    ("q057", "CROSS_LAYER_RELATION", ["q019", "q069"], ["TYPE_SAFETY_RELATED", "LANGUAGE_DESIGN"], "说明类型系统如何影响 Rust 智能指针和 Go 语言特性设计"),
    ("q058", "MECHANISM_APPLICATION", ["q064", "q036"], ["COMPLEXITY_ANALYSIS", "ALGORITHM_DESIGN"], "把主定理用于归并排序复杂度分析，并对比 LCS 这类动态规划问题"),
    ("q059", "PREREQUISITE_PATH", ["q006", "q084"], ["DISCRETE_FOUNDATION", "LOGIC_FOUNDATION"], "从集合运算连接到关系模型和推理规则，说明离散数学基础如何进入数据库与逻辑"),
    ("q060", "MECHANISM_APPLICATION", ["q003", "q092"], ["COMPILER_ARCHITECTURE", "BACKEND_OPTIMIZATION"], "说明 GCC/LLVM 架构如何承载代码优化和寄存器分配"),
    ("q061", "COMPARISON", ["q026", "q058"], ["GREEDY_RELATED", "COMPLEXITY_ANALYSIS"], "比较活动选择、关键路径和主定理分别服务的算法设计与分析问题"),
    ("q062", "CROSS_LAYER_RELATION", ["q008", "q060"], ["ABI_RELATED", "COMPILER_PIPELINE"], "说明链接器与 ABI 如何连接 C 内存布局和编译器架构"),
    ("q063", "COMPARISON", ["q069", "q057"], ["LANGUAGE_FEATURE", "TYPE_MODEL_RELATED"], "把 Go 错误处理哲学放到 Go 语言特性和类型系统设计中比较"),
    ("q064", "MECHANISM_APPLICATION", ["q058", "q036"], ["DIVIDE_AND_CONQUER", "COMPLEXITY_ANALYSIS"], "说明归并排序如何用主定理分析，并与 LCS 的动态规划思路比较"),
    ("q065", "COMPARISON", ["q090", "q099"], ["SEARCH_RELATED", "INDEXING_RELATED"], "比较顺序查找、二叉排序树和 KMP 在查找模型与预处理成本上的差异"),
    ("q066", "PREREQUISITE_PATH", ["q017", "q055"], ["ALGEBRAIC_FOUNDATION", "STRUCTURE_HIERARCHY"], "从群出发解释置换群、环与域的结构扩展关系"),
    ("q067", "COMPARISON", ["q074", "q008"], ["DATA_STRUCTURE_VARIANT", "MEMORY_LAYOUT"], "比较链栈、顺序栈和 C 语言内存布局对栈实现的影响"),
    ("q068", "MECHANISM_APPLICATION", ["q042", "q077"], ["CONCURRENCY_CONTROL", "QUERY_VISIBILITY"], "说明通用 MVCC 概念如何落到具体多版本并发控制和 SQL 查询可见性"),
    ("q069", "COMPARISON", ["q063", "q057"], ["LANGUAGE_FEATURE", "TYPE_MODEL_RELATED"], "比较 Go 语言特性、错误处理哲学和类型系统设计取舍"),
    ("q070", "CROSS_LAYER_RELATION", ["q021", "q028"], ["LIGHTING_MODEL", "MATERIAL_MODEL"], "把 Lambert/Phong/Blinn-Phong 光照、着色频率和 PBR 材质串成渲染知识簇"),
    ("q071", "PREREQUISITE_PATH", ["q083", "q003"], ["HARDWARE_FOUNDATION", "OPTIMIZATION_RELATED"], "从 IEEE754 表示过渡到浮点加法器设计，再联系编译器优化对浮点计算的影响"),
    ("q072", "PREREQUISITE_PATH", ["q020", "q078"], ["SYNTAX_ANALYSIS_RELATED", "SEMANTIC_ANALYSIS"], "说明语法制导定义如何依赖 First/Follow 和二义性处理的语法分析结果"),
    ("q073", "COMMON_MISTAKE", ["q006", "q091"], ["DATA_SECURITY", "AUTHENTICATION_RELATED"], "从 SQL 注入防御追溯到关系模型和消息认证，区分输入校验、权限与完整性保护"),
    ("q074", "COMPARISON", ["q067", "q088"], ["DATA_STRUCTURE_VARIANT", "MEMORY_MODEL_RELATED"], "比较顺序栈、链栈和指针内存模型在空间分配与访问方式上的差异"),
    ("q075", "CROSS_LAYER_RELATION", ["q047", "q076"], ["TRANSPORT_PROTOCOL", "APPLICATION_PROTOCOL"], "把 TCP 三次握手与 IP 层、HTTP 应用层联系起来解释端到端通信"),
    ("q076", "CROSS_LAYER_RELATION", ["q075", "q014"], ["APPLICATION_PROTOCOL", "AUTHENTICATION_FLOW"], "说明 HTTP 如何承载 TCP 连接和 OAuth2/OIDC 的身份流程"),
    ("q077", "MECHANISM_APPLICATION", ["q006", "q093"], ["QUERY_LANGUAGE", "DATABASE_MECHANISM"], "把 SQL SELECT、关系模型和触发器串成从查询到自动化副作用的数据库路径"),
    ("q078", "COMMON_MISTAKE", ["q020", "q096"], ["PARSER_CONFLICT", "GRAMMAR_ANALYSIS"], "从二义性解释 First/Follow 与 LR 分析中的冲突来源和消解方式"),
    ("q079", "CROSS_LAYER_RELATION", ["q019", "q057"], ["LANGUAGE_FEATURE", "TYPE_SAFETY_RELATED"], "说明 Rust 迭代器组合器、智能指针和类型系统如何共同表达零成本抽象"),
    ("q080", "CROSS_LAYER_RELATION", ["q025", "q047"], ["IOT_PROTOCOL", "NETWORK_LAYER_RELATED"], "把 MQTT/CoAP、边缘计算和 IPv4/IPv6 放进物联网通信链路中解释"),
    ("q081", "PREREQUISITE_PATH", ["q005", "q091"], ["NUMBER_THEORY_FOUNDATION", "AUTHENTICATION_RELATED"], "从欧拉函数和模运算理解 RSA，再比较它与 HMAC 的密码学用途"),
    ("q082", "CROSS_LAYER_RELATION", ["q001", "q004"], ["SECURITY_ARCHITECTURE", "ACCESS_CONTROL_RELATED"], "说明软件定义安全如何承接安全策略，并与网络准入控制形成动态防护"),
    ("q083", "PREREQUISITE_PATH", ["q071", "q038"], ["DATA_REPRESENTATION", "HARDWARE_IMPLEMENTATION"], "从编码表示过渡到 IEEE754，再到浮点加法器硬件实现"),
    ("q084", "PREREQUISITE_PATH", ["q059", "q002"], ["LOGIC_FOUNDATION", "PROOF_RELATED"], "说明推理规则如何依赖集合/逻辑基础，并支持复杂度证明中的论证结构"),
    ("q085", "PREREQUISITE_PATH", ["q020", "q078"], ["TOP_DOWN_PARSING", "GRAMMAR_ANALYSIS"], "从 First/Follow 到递归下降，再解释二义性如何影响自顶向下分析"),
    ("q086", "COMPARISON", ["q049", "q099"], ["STRING_ALGORITHM_RELATED", "INDEX_STRUCTURE"], "比较后缀数组算法、后缀数组/树和 KMP 在字符串匹配任务中的分工"),
    ("q087", "COMPARISON", ["q036", "q064"], ["DYNAMIC_PROGRAMMING_RELATED", "SEQUENCE_PROBLEM"], "比较编辑距离、LCS 和归并排序在子问题划分与状态设计上的差异"),
    ("q088", "CROSS_LAYER_RELATION", ["q008", "q074"], ["MEMORY_MODEL_RELATED", "DATA_STRUCTURE_IMPLEMENTATION"], "说明指针算术和内存模型如何影响内存对齐以及顺序栈实现"),
    ("q089", "COMPARISON", ["q030", "q044"], ["ASYNC_MODEL_RELATED", "CONCURRENCY_MODEL"], "比较 Promise 微任务、Python 协程和线程池在并发模型上的差异"),
    ("q090", "COMPARISON", ["q065", "q039"], ["SEARCH_RELATED", "TREE_STRUCTURE"], "比较二叉排序树、顺序查找和图的树结构算法在检索路径上的不同"),
    ("q091", "COMPARISON", ["q081", "q054"], ["AUTHENTICATION_RELATED", "CRYPTOGRAPHY_FOUNDATION"], "比较 MAC/HMAC、RSA 和区块链密码学在完整性、身份和不可否认性上的分工"),
    ("q092", "MECHANISM_APPLICATION", ["q003", "q060"], ["BACKEND_OPTIMIZATION", "COMPILER_ARCHITECTURE"], "说明寄存器分配在代码优化和 GCC/LLVM 后端中的位置"),
    ("q093", "MECHANISM_APPLICATION", ["q077", "q042"], ["DATABASE_MECHANISM", "CONCURRENCY_CONTROL"], "说明 SQL 触发器如何与 SELECT 查询、MVCC 并发可见性相互影响"),
    ("q094", "PREREQUISITE_PATH", ["q037", "q007"], ["AUTOMATA_PIPELINE", "LEXICAL_ANALYSIS"], "从 NFA 到词法分析 DFA，再到 DFA 最小化，说明自动机学习顺序"),
    ("q095", "CROSS_LAYER_RELATION", ["q016", "q025"], ["DISTRIBUTED_SHARDING", "SYSTEM_ARCHITECTURE"], "把一致性哈希与 ZooKeeper 协调、边缘计算架构联系起来解释分布式分片"),
    ("q096", "COMPARISON", ["q020", "q078"], ["BOTTOM_UP_PARSING", "PARSER_CONFLICT"], "比较 LR(0)/SLR(1)、First/Follow 和二义性在语法分析冲突中的作用"),
    ("q097", "CROSS_LAYER_RELATION", ["q028", "q021"], ["GRAPHICS_API", "RENDERING_PIPELINE"], "说明 OpenGL/Vulkan API 如何承载 PBR 材质和着色频率等渲染管线概念"),
    ("q098", "MECHANISM_APPLICATION", ["q053", "q035"], ["CLASSIC_SYNCHRONIZATION", "SYNCHRONIZATION_ABSTRACTION"], "说明读者写者和哲学家进餐如何借助管程与生产者消费者模式理解同步问题"),
    ("q099", "COMPARISON", ["q086", "q049"], ["STRING_MATCHING", "INDEX_STRUCTURE"], "比较 KMP、后缀数组算法和后缀树在字符串匹配中的预处理与查询代价"),
    ("q100", "CROSS_LAYER_RELATION", ["q095", "q009"], ["PROBABILISTIC_STRUCTURE", "DISTRIBUTED_SYSTEM_RELATED"], "把布隆过滤器与一致性哈希、信息论基础联系起来解释概率数据结构在分布式系统中的用途"),
]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _json_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _title_list(items: list[dict[str, Any]]) -> str:
    return "、".join(str(item["expectedTitle"]).strip('"') for item in items)


def _question_text(
    *,
    primary: dict[str, Any],
    related: list[dict[str, Any]],
    intent: str,
    focus: str,
) -> str:
    title = str(primary["expectedTitle"]).strip('"')
    related_titles = _title_list(related)
    if intent == "COMPARISON":
        lead = f"请比较「{title}」与「{related_titles}」的区别和联系"
    elif intent == "PREREQUISITE_PATH":
        lead = f"请构建一条学习路径，说明「{title}」如何依赖或通向「{related_titles}」"
    elif intent == "COMMON_MISTAKE":
        lead = f"请围绕常见误区，说明「{title}」与「{related_titles}」为什么容易被混淆或遗漏"
    elif intent == "COMMUNITY_SUMMARY":
        lead = f"请总结「{title}」所在知识簇，并说明它与「{related_titles}」的协作关系"
    elif intent == "MECHANISM_APPLICATION":
        lead = f"请说明「{title}」在机制落地时如何连接「{related_titles}」"
    else:
        lead = f"请从知识图谱关系角度说明「{title}」与「{related_titles}」之间的多跳联系"
    return (
        f"{lead}。回答时请覆盖：主概念解决的问题、相关概念各自承担的角色、"
        f"它们之间可能的前置/实现/应用关系，以及学习时应该怎样沿这条关系路径串联。"
        f"关系焦点：{focus}。"
    )


def _load_base_questions(path: Path) -> dict[str, dict[str, Any]]:
    payload = _read_json(path)
    questions = payload.get("questions", [])
    if not isinstance(questions, list):
        raise RuntimeError(f"invalid base question set: {path}")
    by_id = {}
    for item in questions:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if item_id:
            by_id[item_id] = item
    missing = sorted({bp[0] for bp in BLUEPRINTS} - set(by_id))
    if missing:
        raise RuntimeError(f"base question set is missing ids: {missing}")
    return by_id


def generate(base_path: Path) -> dict[str, Any]:
    base_questions = _load_base_questions(base_path)
    questions = []
    for index, (source_id, intent, related_ids, relation_types, focus) in enumerate(BLUEPRINTS, start=1):
        primary = base_questions[source_id]
        related = [base_questions[item_id] for item_id in related_ids]
        expected_tags = list(dict.fromkeys(
            [
                *[str(tag) for tag in primary.get("expectedTags", [])],
                *[
                    str(tag)
                    for item in related
                    for tag in item.get("expectedTags", [])[:3]
                ],
            ]
        ))
        questions.append(
            {
                "id": f"grq{index:03d}",
                "sourceQuestionId": source_id,
                "graphIntent": intent,
                "difficulty": "multi_hop" if len(related_ids) >= 2 else "relation",
                "expectedTitle": primary["expectedTitle"],
                "expectedSlug": primary["expectedSlug"],
                "expectedTags": expected_tags[:14],
                "expectedRelatedSlugs": [item["expectedSlug"] for item in related],
                "expectedRelatedTitles": [item["expectedTitle"] for item in related],
                "expectedRelationTypes": relation_types,
                "expectedEvidence": {
                    "primary": primary["expectedSlug"],
                    "related": [item["expectedSlug"] for item in related],
                    "focus": focus,
                },
                "question": _question_text(
                    primary=primary,
                    related=related,
                    intent=intent,
                    focus=focus,
                ),
            }
        )

    return {
        "seed": 20260528,
        "count": len(questions),
        "domain": "COMPUTER_SCIENCE",
        "suite": "graph_rag_100",
        "version": SUITE_VERSION,
        "sourceQuestionSet": str(base_path.as_posix()),
        "evaluationFocus": [
            "multi-hop relation retrieval",
            "cross-course concept linkage",
            "comparison and prerequisite-path questions",
            "graph evidence coverage beyond single-document hit@3",
        ],
        "questionSetHash": _json_hash(questions),
        "questions": questions,
    }


def validate(payload: dict[str, Any]) -> None:
    questions = payload.get("questions", [])
    if len(questions) != 100:
        raise RuntimeError(f"expected 100 questions, got {len(questions)}")
    ids = [item.get("id") for item in questions]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate question ids")
    for item in questions:
        for field in ("id", "expectedTitle", "expectedSlug", "expectedTags", "question"):
            if field not in item:
                raise RuntimeError(f"{item.get('id')} missing benchmark field {field}")
        for field in ("graphIntent", "expectedRelatedSlugs", "expectedRelationTypes", "expectedEvidence"):
            if not item.get(field):
                raise RuntimeError(f"{item.get('id')} missing graph field {field}")
        if item["expectedSlug"] in item["expectedRelatedSlugs"]:
            raise RuntimeError(f"{item.get('id')} relates a node to itself")
    actual_hash = _json_hash(questions)
    if payload.get("questionSetHash") != actual_hash:
        raise RuntimeError("questionSetHash mismatch")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    payload = _read_json(args.output) if args.validate_only else generate(args.base)
    validate(payload)
    if not args.validate_only:
        _write_json(args.output, payload)
    print(f"Validated {payload['count']} graph RAG questions: {args.output}")


if __name__ == "__main__":
    main()
