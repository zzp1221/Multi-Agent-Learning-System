-- =========================================================
-- 智学引擎 - 测试数据清理脚本
-- 生成日期: 2026-06-25
-- 用途: 清理自动化测试产生的用户和相关数据
-- =========================================================

-- 备份提醒: 在生产环境执行前请先备份数据库！

BEGIN;

-- 1. 统计将要删除的数据
SELECT '===== 清理前统计 =====' as info;

SELECT COUNT(*) as test_users_count
FROM app.users
WHERE login_id LIKE 'student_178235%';

SELECT COUNT(*) as test_notes
FROM app.note
WHERE user_id IN (SELECT id FROM app.users WHERE login_id LIKE 'student_178235%');

SELECT COUNT(*) as test_learning_plans
FROM app.learning_plan
WHERE user_id IN (SELECT id FROM app.users WHERE login_id LIKE 'student_178235%');

SELECT COUNT(*) as test_mistake_records
FROM app.mistake_record
WHERE user_id IN (SELECT id FROM app.users WHERE login_id LIKE 'student_178235%');

SELECT COUNT(*) as test_knowledge_nodes
FROM app.learner_knowledge_node
WHERE user_id IN (SELECT id FROM app.users WHERE login_id LIKE 'student_178235%');

-- 2. 删除关联数据 (由于外键级联，删除用户会自动清理大部分关联数据)
-- 但某些表可能需要手动清理

DELETE FROM app.note
WHERE user_id IN (SELECT id FROM app.users WHERE login_id LIKE 'student_178235%');

DELETE FROM app.learning_plan
WHERE user_id IN (SELECT id FROM app.users WHERE login_id LIKE 'student_178235%');

DELETE FROM app.mistake_record
WHERE user_id IN (SELECT id FROM app.users WHERE login_id LIKE 'student_178235%');

DELETE FROM app.learner_knowledge_edge
WHERE user_id IN (SELECT id FROM app.users WHERE login_id LIKE 'student_178235%');

DELETE FROM app.learner_knowledge_node
WHERE user_id IN (SELECT id FROM app.users WHERE login_id LIKE 'student_178235%');

DELETE FROM app.learner_feature
WHERE user_id IN (SELECT id FROM app.users WHERE login_id LIKE 'student_178235%');

DELETE FROM app.autonomous_learning_loop
WHERE user_id IN (SELECT id FROM app.users WHERE login_id LIKE 'student_178235%');

DELETE FROM app.audit_log
WHERE user_id IN (SELECT id FROM app.users WHERE login_id LIKE 'student_178235%');

-- 3. 删除测试用户 (级联删除会自动清理 generated_artifact, smart_engine_task 等)
DELETE FROM app.users
WHERE login_id LIKE 'student_178235%';

-- 4. 显示清理结果
SELECT '===== 清理完成 =====' as info;

SELECT COUNT(*) as remaining_test_users
FROM app.users
WHERE login_id LIKE 'student_178235%';

-- 提交或回滚 (默认回滚以防误操作)
-- 如果确认无误，将 ROLLBACK 改为 COMMIT
ROLLBACK;

-- MongoDB 清理命令 (需要单独在 mongosh 中执行):
--
-- use zhixue;
--
-- // 获取测试用户UUID
-- const testUserIds = [
--   '3f626d19-9870-45fc-bdb1-7b5a3ca208f5',
--   '7023d6d1-83a2-4015-a423-c9b11e8b3d1a',
--   'a62b097c-2c27-4b26-ada2-2f9805b23199',
--   '0180b3b1-71b9-49fd-9a0d-611534b72f3b'
-- ];
--
-- // 删除对话线程
-- db.conversation_threads.deleteMany({userId: {$in: testUserIds}});
--
-- // 删除对话消息
-- db.conversation_messages.deleteMany({userId: {$in: testUserIds}});
--
-- // 删除流式事件
-- db.conversation_stream_events.deleteMany({userId: {$in: testUserIds}});
--
-- // 删除对话摘要
-- db.conversation_summaries.deleteMany({userId: {$in: testUserIds}});
