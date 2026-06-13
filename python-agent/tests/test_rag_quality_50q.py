"""
RAG Quality & Recommendation Accuracy Test — 50 Random Questions
Tests: vector retrieval hit rate, hybrid retrieval quality, resource recommendation relevance.
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2
from retrieval.hybrid_retriever import HybridRetriever
from retrieval.vector_searcher import VectorSearcher
from src.ai_modules.config import get_settings

DB_CONFIG = {"dbname": "zhixue", "user": "postgres", "password": "123456", "host": "localhost", "port": 5432}

# ── 50 test questions across 14 courses ──
QUESTIONS = [
    # 操作系统 (5)
    ("操作系统", "什么是死锁？银行家算法如何避免死锁？", ["死锁", "银行家算法"]),
    ("操作系统", "解释虚拟内存的工作原理和页面置换算法", ["虚拟内存", "页面置换"]),
    ("操作系统", "进程和线程的区别是什么？什么时候用多线程什么时候用多进程？", ["进程", "线程"]),
    ("操作系统", "Docker容器和虚拟机有什么区别？", ["容器", "Docker", "虚拟化"]),
    ("操作系统", "什么是中断？系统调用的执行流程是怎样的？", ["中断", "系统调用"]),

    # 数据库原理 (5)
    ("数据库原理", "什么是MVCC？MySQL和PostgreSQL的MVCC实现有什么区别？", ["MVCC", "并发控制"]),
    ("数据库原理", "B+树索引的原理是什么？为什么MySQL选择B+树而不是红黑树？", ["B+树", "索引"]),
    ("数据库原理", "数据库事务的ACID特性分别是什么？如何实现？", ["事务", "ACID"]),
    ("数据库原理", "什么是分库分表？什么时候需要做分库分表？", ["分库分表", "Sharding"]),
    ("数据库原理", "图数据库Neo4j和关系数据库的区别是什么？", ["图数据库", "Neo4j"]),

    # 数据结构 (5)
    ("数据结构", "红黑树的插入和删除操作是怎么维护平衡的？", ["红黑树", "平衡"]),
    ("数据结构", "哈希表的冲突解决方法有哪些？各自优缺点？", ["哈希表", "冲突"]),
    ("数据结构", "KMP算法的核心思想是什么？next数组怎么求？", ["KMP", "字符串匹配"]),
    ("数据结构", "什么是后缀数组？有什么应用场景？", ["后缀数组", "字符串"]),
    ("数据结构", "并查集的路径压缩和按秩合并是怎么做的？", ["并查集", "路径压缩"]),

    # 计算机网络 (5)
    ("计算机网络", "TCP三次握手和四次挥手的过程是什么？为什么不能两次握手？", ["TCP", "三次握手"]),
    ("计算机网络", "HTTPS的TLS握手过程是怎样的？", ["TLS", "HTTPS", "握手"]),
    ("计算机网络", "QUIC协议相比TCP有什么优势？", ["QUIC", "HTTP3"]),
    ("计算机网络", "什么是CDN？DNS解析的完整过程是怎样的？", ["CDN", "DNS"]),
    ("计算机网络", "MQTT和CoAP协议各适合什么场景？", ["MQTT", "CoAP", "物联网"]),

    # 计算机组成原理 (5)
    ("计算机组成原理", "Cache的工作原理是什么？直接映射、组相联、全相联有什么区别？", ["Cache", "映射"]),
    ("计算机组成原理", "什么是流水线冒险？数据冒险和控制冒险怎么解决？", ["流水线", "冒险"]),
    ("计算机组成原理", "RISC-V架构相比ARM有什么特点？", ["RISC-V", "指令集"]),
    ("计算机组成原理", "IEEE 754浮点数标准是怎么表示浮点数的？", ["浮点数", "IEEE754"]),
    ("计算机组成原理", "GPU和CPU的架构有什么本质区别？", ["GPU", "CPU", "异构计算"]),

    # 编译原理 (5)
    ("编译原理", "LL(1)和LR(1)语法分析的区别是什么？", ["LL", "LR", "语法分析"]),
    ("编译原理", "什么是DFA和NFA？如何从正则表达式构建？", ["DFA", "NFA", "词法分析"]),
    ("编译原理", "编译器的代码优化有哪些常见Pass？", ["优化", "Pass"]),
    ("编译原理", "WebAssembly是什么？它是怎么编译的？", ["WebAssembly", "Wasm"]),
    ("编译原理", "垃圾回收算法有哪些？分代回收是怎么工作的？", ["GC", "垃圾回收", "分代"]),

    # 算法设计与分析 (5)
    ("算法设计与分析", "动态规划和贪心算法的区别是什么？什么时候用哪个？", ["动态规划", "贪心"]),
    ("算法设计与分析", "Dijkstra算法和Bellman-Ford算法的区别？", ["Dijkstra", "Bellman-Ford", "最短路径"]),
    ("算法设计与分析", "什么是NP完全问题？怎么证明一个问题是NPC？", ["NP完全", "计算复杂性"]),
    ("算法设计与分析", "KMP算法的时间复杂度为什么是O(n)？", ["KMP", "复杂度"]),
    ("算法设计与分析", "什么是网络流？最大流算法怎么工作？", ["网络流", "最大流"]),

    # 程序设计 (5)
    ("程序设计", "Go语言的Goroutine和Channel是怎么工作的？", ["Go", "Goroutine", "Channel"]),
    ("程序设计", "Rust的所有权和借用检查是怎么保证内存安全的？", ["Rust", "所有权", "借用"]),
    ("程序设计", "什么是协程？Python的asyncio和Kotlin的协程有什么区别？", ["协程", "asyncio"]),
    ("程序设计", "面向对象的SOLID原则分别是什么？", ["SOLID", "设计原则"]),
    ("程序设计", "函数式编程的核心思想是什么？Monad是什么？", ["函数式编程", "Monad"]),

    # 离散数学 (5)
    ("离散数学", "什么是群论？群、环、域的定义和区别？", ["群论", "环", "域"]),
    ("离散数学", "什么是图论中的欧拉图和哈密顿图？", ["欧拉图", "哈密顿图"]),
    ("离散数学", "什么是谓词逻辑？和命题逻辑有什么区别？", ["谓词逻辑", "命题逻辑"]),
    ("离散数学", "什么是等价关系和偏序关系？", ["等价关系", "偏序关系"]),
    ("离散数学", "什么是模态逻辑？Kripke语义是什么？", ["模态逻辑", "Kripke"]),

    # 信息安全 (5)
    ("信息安全", "RSA加密算法的原理是什么？", ["RSA", "非对称加密"]),
    ("信息安全", "什么是SQL注入？怎么防御？", ["SQL注入", "Web安全"]),
    ("信息安全", "TLS 1.3的握手过程和TLS 1.2有什么区别？", ["TLS", "握手"]),
    ("信息安全", "什么是零信任安全架构？", ["零信任", "安全架构"]),
    ("信息安全", "什么是XSS攻击？有哪几种类型？", ["XSS", "跨站脚本"]),

    # 分布式系统 (5)
    ("分布式系统", "CAP定理是什么？为什么不能同时满足？", ["CAP", "一致性"]),
    ("分布式系统", "Raft共识算法的Leader选举过程是怎样的？", ["Raft", "共识", "选举"]),
    ("分布式系统", "什么是分布式事务？2PC和3PC有什么区别？", ["分布式事务", "2PC", "3PC"]),
    ("分布式系统", "Kafka的架构是怎样的？怎么保证消息不丢失？", ["Kafka", "消息队列"]),
    ("分布式系统", "什么是一致性哈希？怎么解决数据倾斜？", ["一致性哈希", "数据分片"]),

    # 计算机图形学 (5)
    ("计算机图形学", "什么是光栅化？图形渲染管线的完整流程是什么？", ["光栅化", "渲染管线"]),
    ("计算机图形学", "Phong光照模型和PBR物理渲染有什么区别？", ["Phong", "PBR", "光照"]),
    ("计算机图形学", "光线追踪的原理是什么？BVH加速结构怎么工作？", ["光线追踪", "BVH"]),
    ("计算机图形学", "什么是齐次坐标？为什么图形学要用齐次坐标？", ["齐次坐标", "仿射变换"]),
    ("计算机图形学", "什么是延迟渲染？和前向渲染有什么区别？", ["延迟渲染", "前向渲染"]),
]


def test_vector_retrieval():
    """Test vector retrieval quality: does the top-3 contain relevant results?"""
    print("=" * 70)
    print("TEST 1: Vector Retrieval Quality (hits@3, hits@5)")
    print("=" * 70)

    conn = psycopg2.connect(**DB_CONFIG)
    vs = VectorSearcher()

    hits_at_1 = 0
    hits_at_3 = 0
    hits_at_5 = 0
    total = 0
    results_log = []

    try:
        with conn:
            with conn.cursor() as cur:
                for course, question, keywords in QUESTIONS:
                    total += 1
                    # Search knowledge + resource combined
                    results = vs.search_all(cur, question, top_k=5, domain="COMPUTER_SCIENCE")

                    # Check if any result title or slug contains relevant keywords
                    def is_relevant(r):
                        title_lower = r[1].lower()
                        slug_lower = r[0].lower()
                        return any(kw.lower() in title_lower or kw.lower() in slug_lower for kw in keywords)

                    top1_relevant = is_relevant(results[0]) if results else False
                    top3_relevant = any(is_relevant(r) for r in results[:3])
                    top5_relevant = any(is_relevant(r) for r in results[:5])

                    if top1_relevant:
                        hits_at_1 += 1
                    if top3_relevant:
                        hits_at_3 += 1
                    if top5_relevant:
                        hits_at_5 += 1

                    status = "OK" if top3_relevant else "MISS"
                    top_titles = [r[1][:25] for r in results[:3]]
                    sim_scores = [f"{r[2]:.3f}" for r in results[:3]]

                    results_log.append({
                        "q": question[:40],
                        "course": course,
                        "top1": top1_relevant,
                        "top3": top3_relevant,
                        "top5": top5_relevant,
                        "results": list(zip(top_titles, sim_scores)),
                    })

                    print(f"  {status} [{course:10s}] {question[:45]:45s} | top3: {', '.join(top_titles)}")

    finally:
        conn.close()

    print(f"\n--- Vector Retrieval Summary ---")
    print(f"  Total questions: {total}")
    print(f"  hits@1: {hits_at_1}/{total} = {hits_at_1/total*100:.1f}%")
    print(f"  hits@3: {hits_at_3}/{total} = {hits_at_3/total*100:.1f}%")
    print(f"  hits@5: {hits_at_5}/{total} = {hits_at_5/total*100:.1f}%")

    return results_log


def test_hybrid_retrieval():
    """Test hybrid retrieval (grep + vector + graph) quality."""
    print("\n" + "=" * 70)
    print("TEST 2: Hybrid Retrieval Quality (grep_weight=0, vector_weight=5, graph_weight=0.5)")
    print("=" * 70)

    conn = psycopg2.connect(**DB_CONFIG)
    retriever = HybridRetriever(DB_CONFIG, top_k=5)

    hits_at_3 = 0
    total = 0

    try:
        with conn:
            with conn.cursor() as cur:
                for course, question, keywords in QUESTIONS[:20]:  # Test subset for speed
                    total += 1
                    result = retriever.retrieve(cur, question)
                    top = result["top"]

                    def is_relevant(r):
                        title_lower = r[1].lower()
                        slug_lower = r[0].lower()
                        return any(kw.lower() in title_lower or kw.lower() in slug_lower for kw in keywords)

                    top3_relevant = any(is_relevant(r) for r in top[:3])
                    if top3_relevant:
                        hits_at_3 += 1

                    status = "OK" if top3_relevant else "MISS"
                    top_titles = [r[1][:25] for r in top[:3]]
                    print(f"  {status} [{course:10s}] {question[:45]:45s} | {', '.join(top_titles)}")

    finally:
        conn.close()

    print(f"\n--- Hybrid Retrieval Summary ---")
    print(f"  Total questions: {total}")
    print(f"  hits@3: {hits_at_3}/{total} = {hits_at_3/total*100:.1f}%")


def test_resource_recommendation():
    """Test resource recommendation: given a course, does it return relevant resources?"""
    print("\n" + "=" * 70)
    print("TEST 3: Resource Recommendation Accuracy")
    print("=" * 70)

    conn = psycopg2.connect(**DB_CONFIG)

    try:
        with conn:
            with conn.cursor() as cur:
                # Test: for each course, check if vector search returns resources from that course
                courses = ["操作系统", "数据库原理", "信息安全", "分布式系统", "计算机图形学",
                           "数据结构", "计算机网络", "编译原理", "算法设计与分析"]

                correct = 0
                total = 0

                for course in courses:
                    cur.execute("""
                        SELECT rd.title, rd.metadata_json->>'course' as res_course,
                               ROUND((1 - (rc.embedding <=> (
                                   SELECT embedding FROM rag.knowledge_chunk kc2
                                   JOIN rag.knowledge_document kd2 ON kd2.id = kc2.document_id
                                   WHERE kd2.title LIKE %s LIMIT 1
                               )::vector))::numeric, 4) AS similarity
                        FROM rag.resource_chunk rc
                        JOIN rag.resource_document rd ON rd.id = rc.document_id
                        ORDER BY rc.embedding <=> (
                            SELECT embedding FROM rag.knowledge_chunk kc2
                            JOIN rag.knowledge_document kd2 ON kd2.id = kc2.document_id
                            WHERE kd2.title LIKE %s LIMIT 1
                        )::vector
                        LIMIT 5
                    """, (f"%{course}%", f"%{course}%"))

                    results = cur.fetchall()
                    total += 1

                    if results:
                        top_course = results[0][1] if results[0][1] else "N/A"
                        match = top_course == course
                        if match:
                            correct += 1
                        status = "OK" if match else "MISS"
                        titles = [r[0][:30] for r in results[:3]]
                        print(f"  {status} {course:15s} -> top: {titles[0]:30s} (course: {top_course})")
                    else:
                        print(f"  MISS {course:15s} -> no results")

                print(f"\n--- Resource Recommendation Summary ---")
                print(f"  Course match@1: {correct}/{total} = {correct/total*100:.1f}%")

    finally:
        conn.close()


def test_new_course_coverage():
    """Test that new courses (信息安全, 分布式系统, 计算机图形学) are well covered."""
    print("\n" + "=" * 70)
    print("TEST 4: New Course Coverage (wiki + resource)")
    print("=" * 70)

    conn = psycopg2.connect(**DB_CONFIG)
    vs = VectorSearcher()

    new_course_questions = [
        ("信息安全", "零知识证明的原理是什么？"),
        ("信息安全", "什么是同态加密？"),
        ("信息安全", "OAuth2的授权码流程是怎样的？"),
        ("分布式系统", "Paxos算法怎么保证一致性？"),
        ("分布式系统", "Spanner数据库的TrueTime是什么？"),
        ("分布式系统", "什么是Saga模式？"),
        ("计算机图形学", "四元数旋转是怎么表示的？"),
        ("计算机图形学", "什么是PBR物理渲染？"),
        ("计算机图形学", "骨骼动画是怎么实现的？"),
    ]

    try:
        with conn:
            with conn.cursor() as cur:
                for course, q in new_course_questions:
                    results = vs.search_all(cur, q, top_k=3)
                    titles = [r[1][:30] for r in results]
                    sims = [f"{r[2]:.3f}" for r in results]
                    print(f"  [{course:10s}] {q:30s}")
                    for t, s in zip(titles, sims):
                        print(f"    -> {t:30s} sim={s}")
    finally:
        conn.close()


def main():
    settings = get_settings()
    print("RAG Quality & Recommendation Test — 50 Questions")
    print(
        f"Model: {settings.knowledge_embedding_model_name} "
        f"({settings.knowledge_embedding_dimension}-dim)"
    )
    print(f"DB: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
    print()

    # Test 1: Vector retrieval
    log = test_vector_retrieval()

    # Test 2: Hybrid retrieval
    test_hybrid_retrieval()

    # Test 3: Resource recommendation
    test_resource_recommendation()

    # Test 4: New course coverage
    test_new_course_coverage()

    # Save log
    log_path = os.path.join(os.path.dirname(__file__), "rag_test_results.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"\nDetailed log saved to: {log_path}")


if __name__ == "__main__":
    main()
