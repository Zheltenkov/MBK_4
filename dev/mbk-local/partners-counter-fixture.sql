-- Manufactured records for the reader endpoints, not deliveries to a company.
INSERT INTO partner_programs (id,name) VALUES (250,'LOCAL_SYNTHETIC_COUNTER');
INSERT INTO partners (id,name) VALUES (990001,'LOCAL_SYNTHETIC_A'),(990002,'LOCAL_SYNTHETIC_B');
INSERT INTO leads (id,lead_id,partner_program_id,title,phone,data)
VALUES (990001,990001,250,'LOCAL_SYNTHETIC_COUNTER_FIXTURE',80000000001,'{"source":"synthetic_fixture"}');
INSERT INTO sent (id,partner_id,partner_program_id,lead_id,request,response,parent_id) VALUES
 (990001,990001,250,990001,'synthetic_fixture','synthetic_fixture',NULL),
 (990002,990001,250,990001,'synthetic_fixture','synthetic_fixture',NULL),
 (990003,990002,250,990001,'synthetic_fixture','synthetic_fixture',NULL),
 (990004,990002,250,990001,'synthetic_fixture','synthetic_fixture',990003);
