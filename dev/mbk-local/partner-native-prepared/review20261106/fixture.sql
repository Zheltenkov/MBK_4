-- data_source=user_fixture; local test parameters, not production settings.
-- EMPTY dedicated partners_native schema only. NO leads/sent/queues/history seed.
SET NAMES utf8mb4;
START TRANSACTION;
INSERT INTO partner_programs (id,name) VALUES (3,'Local BFL'),(5,'Local BFL Regions');
INSERT INTO tag_types (id,name) VALUES
 (1,'Программа'),(2,'Регион'),(3,'Источник'),(4,'День'),(5,'Сумма долга'),(6,'Время');
INSERT INTO tags (id,tag_type_id,value) VALUES
 (1,4,'Рабочий день'),(2,4,'Выходной'),(3,2,'Москва'),(6,2,'Московская область'),
 (92,2,'Москва и область'),(93,2,'Все, кроме Москвы и области'),(94,5,'От 400к'),
 (100,6,'Дневная кампания'),(950,1,'БФЛ');
INSERT INTO regions (id,tag_id,code,aliases,pattern) VALUES
 (1,3,'77','["г Москва"]','^москва$'),
 (2,6,'50','["Московская обл"]','московск');
INSERT INTO tag_settings (id,tag_id,hands_count,delay_minutes,frozen_days,duplicate_days)
 VALUES (200,950,1,0,1,2);
INSERT INTO campaign_statuses (id,name) VALUES
 (1,'Draft local'),(2,'Active local'),(3,'Paused local'),(4,'Archived local');
INSERT INTO queue_statuses (id,name) VALUES (1,'Queued local'),(2,'Delivered local');
INSERT INTO queue_tag_sources (id,name) VALUES (1,'Lead local'),(2,'Campaign local');
INSERT INTO transaction_types (id,sign,name) VALUES
 (1,1,'Fixture topup'),(2,1,'Fixture correction up'),(3,-1,'Fixture correction down'),(4,-1,'Fixture lead charge');
INSERT INTO partners (id,name,balance,campaign_name)
 VALUES (991001,'LOCAL_NATIVE_BUYER_FIXTURE',100.00,'local-native-review20261106');
INSERT INTO partner_settings (id,partner_id,is_active,partner_program_id,callback_url,email_to_send)
 VALUES (110,991001,1,3,'http://partner-native-buyer:8000/receive',NULL);
INSERT INTO campaigns (id,partner_id,status_id,name,label,priority,price,daily_limit)
 VALUES (950,991001,2,'Local native positive BFL','local-native-review20261106',1,10.00,2);
INSERT INTO campaign_tags (campaign_id,tag_id) VALUES (950,950),(950,92),(950,94);
INSERT INTO balance_transactions (id,partner_id,type_id,amount,comment)
 VALUES (950001,991001,1,100.00,'source=user_fixture; local notional opening balance');
INSERT INTO working_days (day,is_work) VALUES ('2026-11-06',1);
COMMIT;
-- Counters below must all be zero before any native input is sent.
SELECT 'leads' AS evidence_table, COUNT(*) AS rows_present FROM leads
UNION ALL SELECT 'sent',COUNT(*) FROM sent
UNION ALL SELECT 'distribution_queue',COUNT(*) FROM distribution_queue
UNION ALL SELECT 'delivery_history',COUNT(*) FROM delivery_history
UNION ALL SELECT 'campaign_daily_usage',COUNT(*) FROM campaign_daily_usage
UNION ALL SELECT 'jobs',COUNT(*) FROM jobs;
