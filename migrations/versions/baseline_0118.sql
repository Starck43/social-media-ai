-- Name: action_type; Type: TYPE; Schema: {{schema}}; Owner: -
--

CREATE TYPE {{schema}}.action_type AS ENUM (
    'VIEW',
    'CREATE',
    'UPDATE',
    'DELETE',
    'ANALYZE',
    'MODERATE',
    'EXPORT',
    'CONFIGURE'
);


--

-- Name: analysis_period_type; Type: TYPE; Schema: {{schema}}; Owner: -
--

CREATE TYPE {{schema}}.analysis_period_type AS ENUM (
    'DAY',
    'WEEK',
    'MONTH',
    'CUSTOM'
);


--

-- Name: bot_action_status; Type: TYPE; Schema: {{schema}}; Owner: -
--

CREATE TYPE {{schema}}.bot_action_status AS ENUM (
    'PENDING',
    'APPROVED',
    'REJECTED',
    'EXECUTED',
    'FAILED',
    'CLAIMED',
    'UNKNOWN',
    'CANCELLED'
);


--

-- Name: bot_action_type; Type: TYPE; Schema: {{schema}}; Owner: -
--

CREATE TYPE {{schema}}.bot_action_type AS ENUM (
    'COMMENT',
    'REPLY',
    'DIRECT_MESSAGE',
    'POST',
    'REACTION',
    'MODERATION',
    'NOTIFICATION'
);


--

-- Name: bot_trigger_type; Type: TYPE; Schema: {{schema}}; Owner: -
--

CREATE TYPE {{schema}}.bot_trigger_type AS ENUM (
    'KEYWORD_MATCH',
    'SENTIMENT_THRESHOLD',
    'ACTIVITY_SPIKE',
    'USER_MENTION',
    'TIME_BASED',
    'MANUAL'
);


--

-- Name: llm_strategy_type; Type: TYPE; Schema: {{schema}}; Owner: -
--

CREATE TYPE {{schema}}.llm_strategy_type AS ENUM (
    'cost_efficient',
    'quality',
    'multimodal'
);


--

-- Name: notification_type; Type: TYPE; Schema: {{schema}}; Owner: -
--

CREATE TYPE {{schema}}.notification_type AS ENUM (
    'REPORT_READY',
    'MOOD_CHANGE',
    'TREND_ALERT',
    'TOPIC_RESUMED',
    'API_ERROR',
    'CONNECTION_ERROR',
    'SOURCE_INACTIVE',
    'RATE_LIMIT_WARNING',
    'BOT_COMMENT',
    'BOT_SKIPPED',
    'SYSTEM_BACKUP',
    'SYSTEM_UPDATE',
    'SUBSCRIBED_ACTIVITY',
    'KEYWORD_MENTION'
);


--

-- Name: platform_type; Type: TYPE; Schema: {{schema}}; Owner: -
--

CREATE TYPE {{schema}}.platform_type AS ENUM (
    'vk',
    'telegram'
);


--

-- Name: source_type; Type: TYPE; Schema: {{schema}}; Owner: -
--

CREATE TYPE {{schema}}.source_type AS ENUM (
    'USER',
    'GROUP',
    'CHANNEL',
    'CHAT',
    'PAGE',
    'PUBLIC',
    'EVENT',
    'MARKET',
    'ALBUM',
    'SEARCH',
    'SUPERGROUP',
    'BOT',
    'BROADCAST'
);


--

-- Name: check_llm_budget_reservation_receipt(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.check_llm_budget_reservation_receipt() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE receipt RECORD; BEGIN SELECT tenant_id, utc_day, tariff_state INTO receipt FROM {{schema}}.llm_dispatch_receipts WHERE dispatch_id = NEW.dispatch_id; IF NOT FOUND OR receipt.tenant_id IS DISTINCT FROM NEW.tenant_id OR receipt.utc_day IS DISTINCT FROM NEW.utc_day THEN RAISE EXCEPTION 'Reservation receipt binding differs' USING ERRCODE = '23503'; END IF; IF (receipt.tariff_state = 'undefined' AND NEW.ceiling_state <> 'undefined') OR (receipt.tariff_state = 'free' AND (NEW.ceiling_state <> 'bounded' OR NEW.ceiling_usd IS DISTINCT FROM 0 OR NEW.bound_version IS NOT NULL)) OR (receipt.tariff_state = 'known' AND (NEW.ceiling_state = 'undefined' OR (NEW.ceiling_state = 'bounded' AND (NEW.ceiling_usd IS NULL OR NEW.ceiling_usd <= 0)))) THEN RAISE EXCEPTION 'Reservation tariff basis differs' USING ERRCODE = '23514'; END IF; RETURN NEW; END; $$;


--

-- Name: check_llm_outcome_receipt(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.check_llm_outcome_receipt() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE receipt RECORD; BEGIN SELECT tenant_id, pricing_policy_version, prepared_at, input_cost_per_1k, output_cost_per_1k, cache_read_cost_per_1m, cache_write_5m_cost_per_1m, cache_write_1h_cost_per_1m, pricing_token_unit INTO receipt FROM {{schema}}.llm_dispatch_receipts WHERE dispatch_id = NEW.dispatch_id; IF NOT FOUND OR receipt.tenant_id IS DISTINCT FROM NEW.tenant_id OR receipt.pricing_policy_version IS DISTINCT FROM NEW.pricing_policy_version OR NEW.observed_at < receipt.prepared_at THEN RAISE EXCEPTION 'Outcome receipt binding differs' USING ERRCODE = '23503'; END IF; IF NEW.price_source = 'receipt_tariff' AND NEW.cost_certainty = 'free' AND ((receipt.pricing_policy_version = 'provider-price-v1' AND (receipt.input_cost_per_1k IS DISTINCT FROM 0 OR receipt.output_cost_per_1k IS DISTINCT FROM 0)) OR (receipt.pricing_policy_version = 'cache-tariff-v2' AND (receipt.input_cost_per_1k IS DISTINCT FROM 0 OR receipt.output_cost_per_1k IS DISTINCT FROM 0 OR receipt.cache_read_cost_per_1m IS DISTINCT FROM 0 OR receipt.cache_write_5m_cost_per_1m IS DISTINCT FROM 0 OR receipt.cache_write_1h_cost_per_1m IS DISTINCT FROM 0))) THEN RAISE EXCEPTION 'Outcome free tariff differs' USING ERRCODE = '23514'; END IF; IF NEW.provider_price_state = 'resolved' AND (NEW.provider_cost_raw::numeric IS DISTINCT FROM NEW.cost_usd OR (NEW.cost_certainty = 'free') IS DISTINCT FROM (NEW.cost_usd = 0)) THEN RAISE EXCEPTION 'Outcome provider amount differs' USING ERRCODE = '23514'; END IF; RETURN NEW; END; $$;


--

-- Name: check_personal_billing_cutover(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.check_personal_billing_cutover() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE ready boolean; reviewer boolean; BEGIN SELECT is_active AND configured_at IS NOT NULL INTO ready FROM {{schema}}.personal_billing_accounts WHERE id = NEW.account_id; SELECT u.is_active AND r.codename = 'admin' INTO reviewer FROM {{schema}}.users u JOIN {{schema}}.roles r ON r.id = u.role_id WHERE u.id = NEW.reviewed_by_user_id; IF ready IS DISTINCT FROM TRUE OR reviewer IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Cutover requires configured account and active platform reviewer' USING ERRCODE = '23514'; END IF; RETURN NEW; END; $$;


--

-- Name: check_personal_legacy_attribution_consent(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.check_personal_legacy_attribution_consent() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE account_owner integer; source_tenant integer; owner_active boolean; member_active boolean; BEGIN SELECT user_id INTO account_owner FROM {{schema}}.personal_billing_accounts WHERE id = NEW.account_id; IF account_owner IS DISTINCT FROM NEW.consented_by_user_id THEN RAISE EXCEPTION 'Legacy attribution consent must come from the account owner' USING ERRCODE = '23514'; END IF; SELECT is_active INTO owner_active FROM {{schema}}.users WHERE id = NEW.consented_by_user_id; IF owner_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Consent owner is inactive' USING ERRCODE = '23514'; END IF; IF NEW.dispatch_id IS NOT NULL THEN SELECT tenant_id INTO source_tenant FROM {{schema}}.llm_dispatch_receipts WHERE dispatch_id = NEW.dispatch_id; ELSE SELECT tenant_id INTO source_tenant FROM {{schema}}.llm_attempts WHERE attempt_key = NEW.attempt_key; END IF; IF source_tenant IS DISTINCT FROM NEW.tenant_id THEN RAISE EXCEPTION 'Legacy identity workspace differs' USING ERRCODE = '23514'; END IF; SELECT EXISTS (SELECT 1 FROM {{schema}}.tenant_users m WHERE m.tenant_id = NEW.tenant_id AND m.user_id = NEW.consented_by_user_id AND m.is_active) INTO member_active; IF member_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Consent owner has no active workspace membership' USING ERRCODE = '23514'; END IF; RETURN NEW; END; $$;


--

-- Name: check_personal_legacy_attribution_review(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.check_personal_legacy_attribution_review() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE consent record; reviewer_active boolean; BEGIN SELECT * INTO consent FROM {{schema}}.personal_legacy_attribution_consents WHERE id = NEW.consent_id; IF consent.id IS NULL OR consent.source_key IS DISTINCT FROM NEW.source_key OR consent.dispatch_id IS DISTINCT FROM NEW.dispatch_id OR consent.attempt_key IS DISTINCT FROM NEW.attempt_key OR consent.tenant_id IS DISTINCT FROM NEW.tenant_id OR consent.account_id IS DISTINCT FROM NEW.account_id OR consent.consented_by_user_id IS DISTINCT FROM NEW.consented_by_user_id THEN RAISE EXCEPTION 'Reviewed legacy mapping differs from owner consent' USING ERRCODE = '23514'; END IF; SELECT u.is_active AND r.codename = 'admin' INTO reviewer_active FROM {{schema}}.users u JOIN {{schema}}.roles r ON r.id = u.role_id WHERE u.id = NEW.reviewed_by_user_id; IF reviewer_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Legacy mapping reviewer must be active platform admin' USING ERRCODE = '23514'; END IF; RETURN NEW; END; $$;


--

-- Name: check_personal_llm_billing_incident(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.check_personal_llm_billing_incident() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE event_dispatch varchar(32); event_tenant integer; dispatch_tenant integer; reservation_account integer; reservation_day date; BEGIN SELECT dispatch_id, tenant_id INTO event_dispatch, event_tenant FROM {{schema}}.llm_outcome_events WHERE event_id = NEW.event_id; SELECT account_id, utc_day INTO reservation_account, reservation_day FROM {{schema}}.personal_llm_reservations WHERE dispatch_id = NEW.dispatch_id; SELECT tenant_id INTO dispatch_tenant FROM {{schema}}.personal_llm_dispatches WHERE dispatch_id = NEW.dispatch_id; IF event_dispatch IS DISTINCT FROM NEW.dispatch_id OR reservation_account IS DISTINCT FROM NEW.account_id OR reservation_day IS DISTINCT FROM NEW.utc_day OR event_tenant IS DISTINCT FROM dispatch_tenant THEN RAISE EXCEPTION 'Billing incident differs from its stored event/reservation' USING ERRCODE = '23514'; END IF; RETURN NEW; END; $$;


--

-- Name: check_personal_llm_bound_policy(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.check_personal_llm_bound_policy() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE model_provider integer; model_snapshot varchar(200); provider_snapshot varchar(200); reviewer_active boolean; BEGIN SELECT m.provider_id, m.model_id, p.name INTO model_provider, model_snapshot, provider_snapshot FROM {{schema}}.llm_models m JOIN {{schema}}.llm_providers p ON p.id = m.provider_id WHERE m.id = NEW.model_row_id; IF model_provider IS DISTINCT FROM NEW.provider_row_id OR model_snapshot IS DISTINCT FROM NEW.model_id OR provider_snapshot IS DISTINCT FROM NEW.provider_name THEN RAISE EXCEPTION 'Bound policy provider/model mismatch' USING ERRCODE = '23514'; END IF; SELECT u.is_active AND r.codename = 'admin' INTO reviewer_active FROM {{schema}}.users u JOIN {{schema}}.roles r ON r.id = u.role_id WHERE u.id = NEW.reviewed_by_user_id; IF reviewer_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Bound policy reviewer must be active platform admin' USING ERRCODE = '23514'; END IF; RETURN NEW; END; $$;


--

-- Name: check_personal_llm_dispatch_account(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.check_personal_llm_dispatch_account() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE valid boolean; task_tenant integer; task_payer integer; initiator_active boolean; member_active boolean; admin_active boolean; operator_active boolean; receipt_tenant integer; receipt_model integer; receipt_provider integer; BEGIN SELECT tenant_id, llm_model_row_id, llm_provider_row_id INTO receipt_tenant, receipt_model, receipt_provider FROM {{schema}}.llm_dispatch_receipts WHERE dispatch_id = NEW.dispatch_id; IF receipt_tenant IS DISTINCT FROM NEW.tenant_id OR receipt_model IS DISTINCT FROM NEW.llm_model_row_id OR receipt_provider IS DISTINCT FROM NEW.llm_provider_row_id THEN RAISE EXCEPTION 'Personal dispatch must match its legacy receipt identity' USING ERRCODE = '23514'; END IF; SELECT a.is_active AND a.configured_at IS NOT NULL AND a.user_id = NEW.payer_user_id AND u.is_active INTO valid FROM {{schema}}.personal_billing_accounts a JOIN {{schema}}.users u ON u.id = a.user_id WHERE a.id = NEW.account_id; IF valid IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Dispatch payer account differs or is inactive' USING ERRCODE = '23514'; END IF; IF NEW.initiated_by_user_id IS NOT NULL THEN SELECT is_active INTO initiator_active FROM {{schema}}.users WHERE id = NEW.initiated_by_user_id; IF initiator_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Dispatch initiator is inactive' USING ERRCODE = '23514'; END IF; END IF; IF NEW.agent_task_id IS NULL AND NEW.initiated_by_user_id IS DISTINCT FROM NEW.payer_user_id THEN RAISE EXCEPTION 'Non-task dispatch initiator must be the payer' USING ERRCODE = '23514'; END IF; IF NEW.agent_task_id IS NULL AND NEW.purpose = 'admin_probe' THEN SELECT EXISTS (SELECT 1 FROM {{schema}}.users u JOIN {{schema}}.roles r ON r.id = u.role_id WHERE u.id = NEW.payer_user_id AND u.is_active AND r.codename = 'admin') INTO admin_active; IF admin_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Admin probe payer must be active platform admin' USING ERRCODE = '23514'; END IF; END IF; IF NEW.agent_task_id IS NULL AND NEW.purpose <> 'admin_probe' THEN SELECT EXISTS (SELECT 1 FROM {{schema}}.tenant_users m WHERE m.tenant_id = NEW.tenant_id AND m.user_id = NEW.payer_user_id AND m.is_active) INTO member_active; IF member_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Non-task payer workspace membership is inactive' USING ERRCODE = '23514'; END IF; END IF; IF NEW.agent_task_id IS NOT NULL THEN SELECT tenant_id INTO task_tenant FROM {{schema}}.agent_tasks WHERE id = NEW.agent_task_id; IF task_tenant IS DISTINCT FROM NEW.tenant_id THEN RAISE EXCEPTION 'Dispatch task workspace differs' USING ERRCODE = '23503'; END IF; SELECT payer_user_id INTO task_payer FROM {{schema}}.task_billing_payers WHERE agent_task_id = NEW.agent_task_id AND tenant_id = NEW.tenant_id ORDER BY consent_version DESC LIMIT 1; IF task_payer IS DISTINCT FROM NEW.payer_user_id THEN RAISE EXCEPTION 'Dispatch task payer lacks current consent' USING ERRCODE = '23514'; END IF; SELECT EXISTS (SELECT 1 FROM {{schema}}.tenant_users m WHERE m.tenant_id = NEW.tenant_id AND m.user_id = NEW.payer_user_id AND m.is_active) INTO member_active; IF member_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Task payer workspace membership is inactive' USING ERRCODE = '23514'; END IF; IF NEW.initiated_by_user_id IS NOT NULL THEN SELECT EXISTS (SELECT 1 FROM {{schema}}.tenant_users m WHERE m.tenant_id = NEW.tenant_id AND m.user_id = NEW.initiated_by_user_id AND m.is_active) INTO member_active; SELECT EXISTS (SELECT 1 FROM {{schema}}.users u JOIN {{schema}}.roles r ON r.id = u.role_id WHERE u.id = NEW.initiated_by_user_id AND u.is_active AND r.codename IN ('operator', 'admin')) INTO operator_active; IF member_active IS DISTINCT FROM TRUE AND operator_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Task initiator workspace membership is inactive' USING ERRCODE = '23514'; END IF; END IF; END IF; RETURN NEW; END; $$;


--

-- Name: check_personal_llm_ledger_entry(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.check_personal_llm_ledger_entry() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE valid boolean; event_dispatch varchar(32); event_tenant integer; dispatch_tenant integer; BEGIN SELECT (r.account_id = NEW.account_id AND r.utc_day = NEW.utc_day) INTO valid FROM {{schema}}.personal_llm_reservations r WHERE r.dispatch_id = NEW.dispatch_id; IF valid IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Ledger entry differs from its reservation' USING ERRCODE = '23514'; END IF; IF NEW.event_id IS NOT NULL THEN SELECT dispatch_id, tenant_id INTO event_dispatch, event_tenant FROM {{schema}}.llm_outcome_events WHERE event_id = NEW.event_id; SELECT tenant_id INTO dispatch_tenant FROM {{schema}}.personal_llm_dispatches WHERE dispatch_id = NEW.dispatch_id; IF event_dispatch IS DISTINCT FROM NEW.dispatch_id OR event_tenant IS NULL OR dispatch_tenant IS DISTINCT FROM event_tenant THEN RAISE EXCEPTION 'Ledger outcome does not match its dispatch workspace' USING ERRCODE = '23514'; END IF; END IF; RETURN NEW; END; $$;


--

-- Name: check_personal_llm_reservation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.check_personal_llm_reservation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE a_limit numeric; prepared timestamptz; dispatch_account integer; cutover timestamptz; provider_id integer; dispatch_model_row_id integer; dispatch_provider_name varchar(200); dispatch_model_id varchar(200); policy_found boolean; BEGIN SELECT a.daily_limit_usd, d.prepared_at, d.account_id, c.effective_at, d.llm_provider_row_id, d.llm_model_row_id, d.provider_name, d.model_id INTO a_limit, prepared, dispatch_account, cutover, provider_id, dispatch_model_row_id, dispatch_provider_name, dispatch_model_id FROM {{schema}}.personal_llm_dispatches d JOIN {{schema}}.personal_billing_accounts a ON a.id = d.account_id LEFT JOIN {{schema}}.personal_billing_cutovers c ON c.account_id = d.account_id WHERE d.dispatch_id = NEW.dispatch_id; IF dispatch_account IS DISTINCT FROM NEW.account_id OR prepared IS NULL OR NEW.utc_day IS DISTINCT FROM (prepared AT TIME ZONE 'UTC')::date OR cutover IS NULL OR prepared < cutover THEN RAISE EXCEPTION 'Personal reservation has no active reviewed cutover/dispatch binding' USING ERRCODE = '23514'; END IF; IF a_limit IS NULL OR (a_limit = 0 AND NEW.cap_usd IS NOT NULL) OR (a_limit > 0 AND NEW.cap_usd IS DISTINCT FROM a_limit) THEN RAISE EXCEPTION 'Reservation cap differs from configured account policy' USING ERRCODE = '23514'; END IF; IF NEW.ceiling_state = 'bounded' AND NEW.ceiling_usd > 0 THEN SELECT EXISTS (SELECT 1 FROM {{schema}}.personal_llm_bound_policies p WHERE p.provider_row_id = provider_id AND p.model_row_id = dispatch_model_row_id AND p.provider_name = dispatch_provider_name AND p.model_id = dispatch_model_id AND p.version = NEW.bound_version) INTO policy_found; IF policy_found IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Reservation bound is not a reviewed policy' USING ERRCODE = '23514'; END IF; END IF; RETURN NEW; END; $$;


--

-- Name: check_personal_llm_reservation_state_event(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.check_personal_llm_reservation_state_event() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE event_dispatch varchar(32); event_tenant integer; dispatch_tenant integer; BEGIN IF NEW.latest_event_id IS NULL THEN RETURN NEW; END IF; SELECT dispatch_id, tenant_id INTO event_dispatch, event_tenant FROM {{schema}}.llm_outcome_events WHERE event_id = NEW.latest_event_id; SELECT tenant_id INTO dispatch_tenant FROM {{schema}}.personal_llm_dispatches WHERE dispatch_id = NEW.dispatch_id; IF event_dispatch IS DISTINCT FROM NEW.dispatch_id OR event_tenant IS DISTINCT FROM dispatch_tenant THEN RAISE EXCEPTION 'Reservation state event differs from its dispatch workspace' USING ERRCODE = '23514'; END IF; RETURN NEW; END; $$;


--

-- Name: check_task_billing_payer_consent(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.check_task_billing_payer_consent() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE task_tenant integer; account_ready boolean; owner_active boolean; latest_version integer; BEGIN SELECT tenant_id INTO task_tenant FROM {{schema}}.agent_tasks WHERE id = NEW.agent_task_id; IF task_tenant IS DISTINCT FROM NEW.tenant_id THEN RAISE EXCEPTION 'Task payer consent scope differs' USING ERRCODE = '23503'; END IF; SELECT a.is_active AND a.configured_at IS NOT NULL, u.is_active INTO account_ready, owner_active FROM {{schema}}.personal_billing_accounts a JOIN {{schema}}.users u ON u.id = a.user_id WHERE a.user_id = NEW.payer_user_id; IF account_ready IS DISTINCT FROM TRUE OR owner_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Task payer has no configured personal account' USING ERRCODE = '23514'; END IF; SELECT max(consent_version) INTO latest_version FROM {{schema}}.task_billing_payers WHERE agent_task_id = NEW.agent_task_id; IF NEW.consent_version IS DISTINCT FROM coalesce(latest_version, 0) + 1 THEN RAISE EXCEPTION 'Task payer consent version is not next' USING ERRCODE = '23514'; END IF; RETURN NEW; END; $$;


--

-- Name: guard_action_audit_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.guard_action_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN IF TG_OP = 'DELETE' AND current_setting('app.action_audit_retention', true) = 'on' THEN RETURN OLD; END IF; RAISE EXCEPTION 'Action audit evidence is immutable outside retention cleanup' USING ERRCODE = '55000'; END; $$;


--

-- Name: guard_personal_catalog_owner(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.guard_personal_catalog_owner() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
          IF NEW.owner_user_id<>OLD.owner_user_id THEN
            RAISE EXCEPTION 'Personal catalog ownership is immutable' USING ERRCODE='23514';
          END IF;
          IF TG_TABLE_NAME='llm_models' THEN
            IF NEW.provider_id<>OLD.provider_id THEN
              RAISE EXCEPTION 'Model provider is immutable' USING ERRCODE='23514';
            END IF;
          END IF;
          RETURN NEW;
        END $$;


--

-- Name: guard_task_binding(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.guard_task_binding() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE task_tenant integer; source_tenant integer; source_owner integer;
        BEGIN
          IF TG_TABLE_NAME='agent_task_sources' THEN
            SELECT tenant_id INTO task_tenant FROM {{schema}}.agent_tasks WHERE id=NEW.agent_task_id FOR KEY SHARE;
            SELECT tenant_id, owner_task_id INTO source_tenant, source_owner FROM {{schema}}.sources WHERE id=NEW.source_id FOR SHARE;
            IF task_tenant IS NULL OR source_tenant IS NULL OR task_tenant<>source_tenant
               OR (source_owner IS NOT NULL AND source_owner<>NEW.agent_task_id) THEN
              RAISE EXCEPTION 'Invalid workspace task source binding' USING ERRCODE='23514';
            END IF;
          ELSIF TG_TABLE_NAME='agent_tasks' THEN
            IF TG_OP='UPDATE' AND NEW.tenant_id<>OLD.tenant_id THEN
              RAISE EXCEPTION 'Workspace ownership is immutable' USING ERRCODE='23514';
            END IF;
            IF NEW.agent_scenario_id IS NOT NULL THEN
              PERFORM 1 FROM {{schema}}.agent_scenarios WHERE id=NEW.agent_scenario_id AND tenant_id=NEW.tenant_id FOR KEY SHARE;
              IF NOT FOUND THEN RAISE EXCEPTION 'Invalid workspace scenario binding' USING ERRCODE='23514'; END IF;
            END IF;
          ELSE
            IF TG_TABLE_NAME='sources' THEN
              IF NEW.owner_task_id IS NOT NULL THEN
                PERFORM 1 FROM {{schema}}.agent_tasks WHERE id=NEW.owner_task_id AND tenant_id=NEW.tenant_id FOR KEY SHARE;
                IF NOT FOUND THEN RAISE EXCEPTION 'Invalid source owning task' USING ERRCODE='23514'; END IF;
                IF EXISTS (SELECT 1 FROM {{schema}}.agent_task_sources WHERE source_id=NEW.id AND agent_task_id<>NEW.owner_task_id) THEN
                  RAISE EXCEPTION 'Task-owned source has foreign links' USING ERRCODE='23514';
                END IF;
              END IF;
            END IF;
            IF TG_OP='UPDATE' THEN
              IF NEW.tenant_id<>OLD.tenant_id THEN
                RAISE EXCEPTION 'Workspace ownership is immutable' USING ERRCODE='23514';
              END IF;
            END IF;
          END IF;
          RETURN NEW;
        END $$;


--

-- Name: reject_llm_budget_reservation_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.reject_llm_budget_reservation_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'Budget reservation is immutable' USING ERRCODE = '55000'; END; $$;


--

-- Name: reject_llm_outcome_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.reject_llm_outcome_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'Outcome evidence is immutable' USING ERRCODE = '55000'; END; $$;


--

-- Name: reject_llm_receipt_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.reject_llm_receipt_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'Dispatch receipts are immutable' USING ERRCODE = '55000'; END; $$;


--

-- Name: reject_personal_billing_cutovers_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.reject_personal_billing_cutovers_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'Billing cutovers are immutable' USING ERRCODE = '55000'; END; $$;


--

-- Name: reject_personal_legacy_attribution_consents_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.reject_personal_legacy_attribution_consents_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'Legacy billing attribution evidence is immutable' USING ERRCODE = '55000'; END; $$;


--

-- Name: reject_personal_legacy_attributions_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.reject_personal_legacy_attributions_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'Legacy billing attribution evidence is immutable' USING ERRCODE = '55000'; END; $$;


--

-- Name: reject_personal_llm_billing_incidents_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.reject_personal_llm_billing_incidents_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'Billing incidents are immutable' USING ERRCODE = '55000'; END; $$;


--

-- Name: reject_personal_llm_bound_policies_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.reject_personal_llm_bound_policies_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'Bound policies are immutable' USING ERRCODE = '55000'; END; $$;


--

-- Name: reject_personal_llm_dispatches_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.reject_personal_llm_dispatches_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'Personal dispatch attribution is immutable' USING ERRCODE = '55000'; END; $$;


--

-- Name: reject_personal_llm_ledger_entries_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.reject_personal_llm_ledger_entries_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'Personal ledger entries are immutable' USING ERRCODE = '55000'; END; $$;


--

-- Name: reject_personal_llm_reservations_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.reject_personal_llm_reservations_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'Personal reservations are immutable' USING ERRCODE = '55000'; END; $$;


--

-- Name: reject_task_billing_payers_mutation(); Type: FUNCTION; Schema: {{schema}}; Owner: -
--

CREATE FUNCTION {{schema}}.reject_task_billing_payers_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'Task payer consents are immutable' USING ERRCODE = '55000'; END; $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--

-- Name: action_audit_events; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.action_audit_events (
    id integer NOT NULL,
    tenant_id integer NOT NULL,
    event_id character varying(32) NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_kind character varying(16) NOT NULL,
    actor_id character varying(64) NOT NULL,
    action character varying(80) NOT NULL,
    resource_kind character varying(40),
    resource_id character varying(128),
    outcome character varying(16) NOT NULL,
    reason_code character varying(64),
    request_id character varying(128),
    job_id integer,
    receipt_ref character varying(160),
    CONSTRAINT ck_action_audit_events_action CHECK (((action)::text = 'scheduling.task_admit'::text)),
    CONSTRAINT ck_action_audit_events_actor_id CHECK (((((actor_kind)::text = 'user'::text) AND ((actor_id)::text ~ '^[1-9][0-9]{0,9}$'::text)) OR (((actor_kind)::text = 'service'::text) AND ((actor_id)::text = 'task_runner'::text)) OR (((actor_kind)::text = 'scheduler'::text) AND ((actor_id)::text = 'scheduler'::text)))),
    CONSTRAINT ck_action_audit_events_actor_kind CHECK (((actor_kind)::text = ANY ((ARRAY['user'::character varying, 'service'::character varying, 'scheduler'::character varying])::text[]))),
    CONSTRAINT ck_action_audit_events_event_id CHECK (((event_id)::text ~ '^[0-9a-f]{32}$'::text)),
    CONSTRAINT ck_action_audit_events_job_id CHECK (((job_id IS NULL) OR ((job_id > 0) AND (job_id <= 2147483647)))),
    CONSTRAINT ck_action_audit_events_outcome CHECK (((outcome)::text = ANY ((ARRAY['succeeded'::character varying, 'unknown'::character varying])::text[]))),
    CONSTRAINT ck_action_audit_events_reason_code CHECK (((((outcome)::text = 'succeeded'::text) AND (reason_code IS NULL)) OR (((outcome)::text = 'unknown'::text) AND ((reason_code)::text = 'launch_unconfirmed'::text)))),
    CONSTRAINT ck_action_audit_events_receipt_ref CHECK ((receipt_ref IS NULL)),
    CONSTRAINT ck_action_audit_events_request_id CHECK ((request_id IS NULL)),
    CONSTRAINT ck_action_audit_events_resource_id CHECK (((resource_id IS NULL) OR ((resource_id)::text ~ '^[1-9][0-9]{0,9}$'::text))),
    CONSTRAINT ck_action_audit_events_resource_kind CHECK (((resource_kind IS NULL) OR ((resource_kind)::text = 'agent_task'::text)))
);


--

-- Name: action_audit_events_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.action_audit_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: action_audit_events_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.action_audit_events_id_seq OWNED BY {{schema}}.action_audit_events.id;


--

-- Name: agent_feedback; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.agent_feedback (
    id integer NOT NULL,
    session_id integer NOT NULL,
    message_id integer,
    vote character varying(10) NOT NULL,
    note text,
    voter_channel character varying(20),
    voter_external_id character varying(100),
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: COLUMN agent_feedback.vote; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_feedback.vote IS 'good | bad';


--

-- Name: COLUMN agent_feedback.note; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_feedback.note IS 'Free-text complaint attached to /bad';


--

-- Name: COLUMN agent_feedback.voter_external_id; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_feedback.voter_external_id IS 'Channel user id of the voter';


--

-- Name: agent_feedback_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.agent_feedback_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: agent_feedback_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.agent_feedback_id_seq OWNED BY {{schema}}.agent_feedback.id;


--

-- Name: agent_memory; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.agent_memory (
    id integer NOT NULL,
    user_id integer,
    scope character varying(20) DEFAULT 'global'::character varying NOT NULL,
    key character varying(100) NOT NULL,
    value text,
    source character varying(20) DEFAULT 'manual'::character varying NOT NULL,
    confidence double precision DEFAULT '1'::double precision NOT NULL,
    evidence_message_id integer,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: COLUMN agent_memory.user_id; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_memory.user_id IS 'Personal memory owner; NULL is the legacy owner-only bucket';


--

-- Name: COLUMN agent_memory.source; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_memory.source IS 'manual (memory_set) | learn (extracted from chat) | reflect (post-dedup)';


--

-- Name: COLUMN agent_memory.evidence_message_id; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_memory.evidence_message_id IS 'Chat message this fact was learned from (FK is SET NULL: messages can be cleared)';


--

-- Name: agent_memory_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.agent_memory_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: agent_memory_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.agent_memory_id_seq OWNED BY {{schema}}.agent_memory.id;


--

-- Name: agent_messages; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.agent_messages (
    id integer NOT NULL,
    session_id integer NOT NULL,
    role character varying(20) NOT NULL,
    content text,
    tool_calls json,
    tool_name character varying(64),
    tokens integer,
    cost double precision,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: agent_messages_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.agent_messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: agent_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.agent_messages_id_seq OWNED BY {{schema}}.agent_messages.id;


--

-- Name: agent_scenarios; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.agent_scenarios (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    content_types json,
    analysis_types json,
    scope json,
    base_prompt text,
    media_overrides json,
    summary_prompt text,
    max_tokens integer,
    output_schema json,
    is_active boolean,
    is_default boolean DEFAULT false NOT NULL,
    text_llm_model_id integer,
    image_llm_model_id integer,
    video_llm_model_id integer,
    llm_strategy {{schema}}.llm_strategy_type,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: COLUMN agent_scenarios.base_prompt; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_scenarios.base_prompt IS 'Core LLM instruction for analysis. If null, uses default.';


--

-- Name: COLUMN agent_scenarios.media_overrides; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_scenarios.media_overrides IS 'Per-media-type prompt overrides, e.g. {"image": "...", "video": "..."}';


--

-- Name: COLUMN agent_scenarios.summary_prompt; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_scenarios.summary_prompt IS 'Custom prompt for unified summary. If null, uses default.';


--

-- Name: COLUMN agent_scenarios.max_tokens; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_scenarios.max_tokens IS 'Max tokens for LLM responses in this scenario';


--

-- Name: COLUMN agent_scenarios.output_schema; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_scenarios.output_schema IS 'JSON Schema for structured LLM output (lazily generated from analysis_types)';


--

-- Name: COLUMN agent_scenarios.is_default; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_scenarios.is_default IS 'Default scenario for the tenant when a source has none assigned';


--

-- Name: COLUMN agent_scenarios.text_llm_model_id; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_scenarios.text_llm_model_id IS 'Specific LLM model for text analysis';


--

-- Name: COLUMN agent_scenarios.image_llm_model_id; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_scenarios.image_llm_model_id IS 'Specific LLM model for image analysis';


--

-- Name: COLUMN agent_scenarios.video_llm_model_id; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_scenarios.video_llm_model_id IS 'Specific LLM model for video analysis';


--

-- Name: agent_scenarios_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.agent_scenarios_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: agent_scenarios_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.agent_scenarios_id_seq OWNED BY {{schema}}.agent_scenarios.id;


--

-- Name: agent_sessions; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.agent_sessions (
    id integer NOT NULL,
    channel character varying(20) NOT NULL,
    chat_id character varying(100) NOT NULL,
    kind character varying(20) DEFAULT 'private'::character varying NOT NULL,
    is_owner boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    state json,
    last_message_at timestamp with time zone,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: agent_sessions_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.agent_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: agent_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.agent_sessions_id_seq OWNED BY {{schema}}.agent_sessions.id;


--

-- Name: agent_task_sources; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.agent_task_sources (
    agent_task_id integer NOT NULL,
    source_id integer NOT NULL
);


--

-- Name: agent_tasks; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.agent_tasks (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    launch_key character varying(64),
    launch_fingerprint character varying(64),
    launch_job_id integer,
    cron_expr character varying(100) NOT NULL,
    job_type character varying(20) NOT NULL,
    payload json DEFAULT '{}'::json NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    next_run_at timestamp with time zone,
    last_run_at timestamp with time zone,
    last_status character varying(20),
    last_error text,
    trigger_type {{schema}}.bot_trigger_type,
    trigger_config json DEFAULT '{}'::json,
    action_type {{schema}}.bot_action_type,
    rate_limit_per_hour integer,
    cooldown_seconds integer,
    dry_run boolean DEFAULT false NOT NULL,
    agent_scenario_id integer,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: COLUMN agent_tasks.rate_limit_per_hour; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_tasks.rate_limit_per_hour IS 'Max actions per hour for this task';


--

-- Name: COLUMN agent_tasks.cooldown_seconds; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.agent_tasks.cooldown_seconds IS 'Min seconds between actions';


--

-- Name: agent_tasks_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.agent_tasks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: agent_tasks_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.agent_tasks_id_seq OWNED BY {{schema}}.agent_tasks.id;


--

-- Name: ai_analytics; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.ai_analytics (
    id integer NOT NULL,
    source_id integer NOT NULL,
    analysis_date date DEFAULT CURRENT_DATE,
    period_type {{schema}}.analysis_period_type NOT NULL,
    content_hash character varying(64),
    analysis_fingerprint character varying(64) DEFAULT 'legacy'::character varying NOT NULL,
    topic_chain_id character varying(100),
    chain_label character varying(255),
    normalized_label character varying(255),
    parent_analysis_id integer,
    summary_data json DEFAULT '{}'::jsonb NOT NULL,
    response_payload json,
    main_topics json,
    prompt_text text,
    llm_model character varying(100),
    provider_type character varying(100),
    media_types json,
    request_tokens integer,
    response_tokens integer,
    estimated_cost numeric(14,6),
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: COLUMN ai_analytics.content_hash; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.ai_analytics.content_hash IS 'SHA256 hex digest of the analyzed content payload for deduplication';


--

-- Name: COLUMN ai_analytics.analysis_fingerprint; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.ai_analytics.analysis_fingerprint IS 'Versioned semantic identity of the analysis context; legacy rows are unattributed';


--

-- Name: COLUMN ai_analytics.chain_label; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.ai_analytics.chain_label IS 'Human-readable name of the topic chain (latest analysis_title or top topic)';


--

-- Name: COLUMN ai_analytics.normalized_label; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.ai_analytics.normalized_label IS 'Normalized topic label for chain deduplication (lowercase, ё→е, stemmed)';


--

-- Name: COLUMN ai_analytics.main_topics; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.ai_analytics.main_topics IS 'Main topics extracted from AI analysis for theme matching';


--

-- Name: COLUMN ai_analytics.estimated_cost; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.ai_analytics.estimated_cost IS 'Estimated cost in USD cents at 1e-8 USD precision (NULL = unknown or free)';


--

-- Name: ai_analytics_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.ai_analytics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: ai_analytics_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.ai_analytics_id_seq OWNED BY {{schema}}.ai_analytics.id;


--

-- Name: analyzed_content_receipts; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.analyzed_content_receipts (
    id integer NOT NULL,
    source_id integer NOT NULL,
    content_hash character varying(64) NOT NULL,
    analysis_fingerprint character varying(64) NOT NULL,
    scenario_id integer,
    analytics_id integer,
    analyzed_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id integer NOT NULL
);


--

-- Name: analyzed_content_receipts_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.analyzed_content_receipts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: analyzed_content_receipts_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.analyzed_content_receipts_id_seq OWNED BY {{schema}}.analyzed_content_receipts.id;


--

-- Name: bot_actions; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.bot_actions (
    id integer NOT NULL,
    agent_scenario_id integer,
    agent_task_id integer,
    source_id integer,
    analytics_id integer,
    action_type {{schema}}.bot_action_type,
    status {{schema}}.bot_action_status DEFAULT 'PENDING'::{{schema}}.bot_action_status NOT NULL,
    capability character varying(100) DEFAULT 'social.legacy'::character varying NOT NULL,
    origin character varying(20) DEFAULT 'task'::character varying NOT NULL,
    target_ref character varying(300),
    idempotency_key character varying(200),
    payload_hash character varying(64),
    approved_payload_hash character varying(64),
    approved_user_id integer,
    approval_expires_at timestamp with time zone,
    claimed_at timestamp with time zone,
    claim_expires_at timestamp with time zone,
    claim_token_hash character varying(64),
    executed_at timestamp with time zone,
    history json DEFAULT '[]'::json NOT NULL,
    origin_snapshot json,
    payload json DEFAULT '{}'::jsonb NOT NULL,
    result json,
    error text,
    dry_run boolean DEFAULT true NOT NULL,
    confirmed_by integer,
    confirmed_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: bot_actions_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.bot_actions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: bot_actions_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.bot_actions_id_seq OWNED BY {{schema}}.bot_actions.id;


--

-- Name: collected_items; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.collected_items (
    id integer NOT NULL,
    run_id integer,
    source_id integer NOT NULL,
    external_id character varying(255),
    content_hash character varying(64) NOT NULL,
    platform character varying(50),
    content_type character varying(20) DEFAULT 'post'::character varying NOT NULL,
    parent_external_id character varying(255),
    lane_id character varying(100),
    selection_status character varying(20),
    selection_reason character varying(64),
    published_at timestamp with time zone,
    media_type character varying(20),
    attachments json,
    text text,
    metrics json,
    author json,
    permalink character varying(500),
    analyze_attempts integer DEFAULT 0 NOT NULL,
    give_up_after_attempts integer DEFAULT 3 NOT NULL,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: collected_items_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.collected_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: collected_items_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.collected_items_id_seq OWNED BY {{schema}}.collected_items.id;


--

-- Name: collection_candidates; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.collection_candidates (
    id integer NOT NULL,
    platform character varying(50) NOT NULL,
    kind character varying(20) NOT NULL,
    external_id character varying(255) NOT NULL,
    name character varying(255),
    url character varying(500),
    city character varying(255),
    city_source character varying(50),
    city_confidence double precision,
    metrics json,
    evidence json,
    status character varying(20) DEFAULT 'new'::character varying NOT NULL,
    source_id integer,
    discovered_by character varying(100),
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: collection_candidates_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.collection_candidates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: collection_candidates_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.collection_candidates_id_seq OWNED BY {{schema}}.collection_candidates.id;


--

-- Name: collection_lane_runs; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.collection_lane_runs (
    id integer NOT NULL,
    job_id integer,
    task_id integer,
    lane_id character varying(100) NOT NULL,
    lane_type character varying(30) NOT NULL,
    query text,
    fetched integer DEFAULT 0 NOT NULL,
    selected integer DEFAULT 0 NOT NULL,
    needs_review integer DEFAULT 0 NOT NULL,
    rejected integer DEFAULT 0 NOT NULL,
    limit_count integer,
    limit_reached boolean DEFAULT false NOT NULL,
    next_cursor character varying(500),
    total_available integer,
    error_code character varying(50),
    unsupported_reason character varying(255),
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: collection_lane_runs_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.collection_lane_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: collection_lane_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.collection_lane_runs_id_seq OWNED BY {{schema}}.collection_lane_runs.id;


--

-- Name: collection_seen; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.collection_seen (
    id integer NOT NULL,
    source_id integer NOT NULL,
    content_type character varying(20) NOT NULL,
    external_id character varying(255) NOT NULL,
    content_hash character varying(64) NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    status character varying(20) DEFAULT 'new'::character varying NOT NULL,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: collection_seen_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.collection_seen_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: collection_seen_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.collection_seen_id_seq OWNED BY {{schema}}.collection_seen.id;


--

-- Name: digest_runs; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.digest_runs (
    id integer NOT NULL,
    agent_task_id integer,
    period character varying(10) NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    channel character varying(20) NOT NULL,
    chat_id character varying(100),
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    message_id character varying(100),
    content text,
    error text,
    delivery_state jsonb,
    llm_cost double precision,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: digest_runs_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.digest_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: digest_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.digest_runs_id_seq OWNED BY {{schema}}.digest_runs.id;


--

-- Name: entitlement_usage; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.entitlement_usage (
    id integer NOT NULL,
    capability character varying(160) NOT NULL,
    job_id integer NOT NULL,
    task_id integer,
    initiated_by_user_id integer,
    plan character varying(20) NOT NULL,
    catalog_version integer NOT NULL,
    admitted_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id integer NOT NULL
);


--

-- Name: entitlement_usage_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.entitlement_usage_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: entitlement_usage_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.entitlement_usage_id_seq OWNED BY {{schema}}.entitlement_usage.id;


--

-- Name: jobs; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.jobs (
    id integer NOT NULL,
    agent_task_id integer,
    initiated_by_user_id integer,
    job_type character varying(20) NOT NULL,
    payload json DEFAULT '{}'::json NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    run_at timestamp with time zone NOT NULL,
    locked_at timestamp with time zone,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    result json,
    error text,
    llm_cost double precision,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: jobs_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.jobs_id_seq OWNED BY {{schema}}.jobs.id;


--

-- Name: llm_attempts; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.llm_attempts (
    id integer NOT NULL,
    attempt_key character varying(64) NOT NULL,
    purpose character varying(32) NOT NULL,
    stage character varying(32) NOT NULL,
    source_id integer,
    analysis_date date,
    job_id integer,
    agent_task_id integer,
    llm_model_row_id integer,
    model_id character varying(200),
    provider_name character varying(200),
    input_cost_per_1k numeric(18,8),
    output_cost_per_1k numeric(18,8),
    cache_read_cost_per_1m numeric(18,8),
    cache_write_5m_cost_per_1m numeric(18,8),
    cache_write_1h_cost_per_1m numeric(18,8),
    cache_usage jsonb,
    pricing_policy_version character varying(32),
    pricing_token_unit integer,
    prompt_tokens integer,
    completion_tokens integer,
    cost_usd numeric(18,9),
    cost_certainty character varying(32) NOT NULL,
    outcome character varying(32) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id integer NOT NULL,
    CONSTRAINT ck_llm_attempts_cache_rates CHECK ((((cache_read_cost_per_1m IS NULL) OR (cache_read_cost_per_1m >= (0)::numeric)) AND ((cache_write_5m_cost_per_1m IS NULL) OR (cache_write_5m_cost_per_1m >= (0)::numeric)) AND ((cache_write_1h_cost_per_1m IS NULL) OR (cache_write_1h_cost_per_1m >= (0)::numeric)))),
    CONSTRAINT ck_llm_attempts_pricing_token_unit CHECK (COALESCE(((((pricing_policy_version IS NULL) OR ((pricing_policy_version)::text = 'provider-price-v1'::text)) AND ((pricing_token_unit IS NULL) OR (pricing_token_unit = 1000)) AND (cache_usage IS NULL) AND (cache_read_cost_per_1m IS NULL) AND (cache_write_5m_cost_per_1m IS NULL) AND (cache_write_1h_cost_per_1m IS NULL)) OR (((pricing_policy_version)::text = 'cache-tariff-v2'::text) AND ((pricing_token_unit = ANY (ARRAY[1000, 1000000])) OR ((pricing_token_unit IS NULL) AND (input_cost_per_1k IS NULL) AND (output_cost_per_1k IS NULL) AND (cache_read_cost_per_1m IS NULL) AND (cache_write_5m_cost_per_1m IS NULL) AND (cache_write_1h_cost_per_1m IS NULL))))), false))
);


--

-- Name: COLUMN llm_attempts.attempt_key; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.llm_attempts.attempt_key IS 'SHA256 of the attempt''s content identity; repeat writes are idempotent';


--

-- Name: COLUMN llm_attempts.purpose; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.llm_attempts.purpose IS '''analysis'' | future: ''digest'' | ''learn'' | ''agent_chat''';


--

-- Name: COLUMN llm_attempts.stage; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.llm_attempts.stage IS '''text_analysis'' | ''image_analysis'' | ''video_analysis'' | ''unified_summary''';


--

-- Name: COLUMN llm_attempts.cost_certainty; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.llm_attempts.cost_certainty IS '''priced'' | ''free'' | ''unknown_tariff'' | ''unknown_usage'' | ''not_dispatched''';


--

-- Name: COLUMN llm_attempts.outcome; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.llm_attempts.outcome IS '''succeeded'' | ''failed_output'' | ''failed_transport'' | ''unknown''';


--

-- Name: llm_attempts_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.llm_attempts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: llm_attempts_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.llm_attempts_id_seq OWNED BY {{schema}}.llm_attempts.id;


--

-- Name: llm_budget_days; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.llm_budget_days (
    id integer NOT NULL,
    tenant_id integer NOT NULL,
    utc_day date NOT NULL,
    settled_usd numeric(18,9) NOT NULL,
    reserved_usd numeric(18,9) NOT NULL,
    undefined_count integer NOT NULL,
    unbounded_count integer NOT NULL,
    frozen boolean DEFAULT true NOT NULL,
    baseline_version character varying(64) NOT NULL,
    CONSTRAINT ck_llm_budget_days_amounts CHECK (((settled_usd >= (0)::numeric) AND (settled_usd <= 999999999.999999999) AND (reserved_usd >= (0)::numeric) AND (reserved_usd <= 999999999.999999999))),
    CONSTRAINT ck_llm_budget_days_counts CHECK (((undefined_count >= 0) AND (unbounded_count >= 0)))
);


--

-- Name: llm_budget_days_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.llm_budget_days_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: llm_budget_days_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.llm_budget_days_id_seq OWNED BY {{schema}}.llm_budget_days.id;


--

-- Name: llm_budget_reservations; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.llm_budget_reservations (
    id integer NOT NULL,
    tenant_id integer NOT NULL,
    utc_day date NOT NULL,
    dispatch_id character varying(32) NOT NULL,
    ceiling_state character varying(16) NOT NULL,
    ceiling_usd numeric(18,9),
    bound_version character varying(64),
    undefined_policy_version character varying(32) NOT NULL,
    cap_usd numeric(18,9),
    CONSTRAINT ck_llm_budget_reservations_amounts CHECK ((((ceiling_usd IS NULL) OR ((ceiling_usd >= (0)::numeric) AND (ceiling_usd <= 999999999.999999999))) AND ((cap_usd IS NULL) OR ((cap_usd > (0)::numeric) AND (cap_usd <= 999999999.999999999))))),
    CONSTRAINT ck_llm_budget_reservations_bound CHECK (((((ceiling_state)::text = 'bounded'::text) AND (ceiling_usd IS NOT NULL) AND ((ceiling_usd = (0)::numeric) OR (bound_version IS NOT NULL))) OR (((ceiling_state)::text = 'unbounded'::text) AND (ceiling_usd IS NULL) AND (bound_version IS NULL) AND (cap_usd IS NULL)) OR (((ceiling_state)::text = 'undefined'::text) AND (ceiling_usd IS NOT NULL) AND (ceiling_usd = (0)::numeric) AND (bound_version IS NULL)))),
    CONSTRAINT ck_llm_budget_reservations_identity CHECK (((dispatch_id)::text ~ '^[0-9a-f]{32}$'::text)),
    CONSTRAINT ck_llm_budget_reservations_policy CHECK (((undefined_policy_version)::text = 'owner-undefined-allow-v1'::text)),
    CONSTRAINT ck_llm_budget_reservations_state CHECK (((ceiling_state)::text = ANY ((ARRAY['bounded'::character varying, 'unbounded'::character varying, 'undefined'::character varying])::text[])))
);


--

-- Name: llm_budget_reservations_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.llm_budget_reservations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: llm_budget_reservations_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.llm_budget_reservations_id_seq OWNED BY {{schema}}.llm_budget_reservations.id;


--

-- Name: llm_dispatch_receipts; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.llm_dispatch_receipts (
    id integer NOT NULL,
    tenant_id integer NOT NULL,
    dispatch_id character varying(32) NOT NULL,
    purpose character varying(32) NOT NULL,
    stage character varying(32) NOT NULL,
    prepared_at timestamp with time zone NOT NULL,
    utc_day date NOT NULL,
    llm_model_row_id integer NOT NULL,
    llm_provider_row_id integer NOT NULL,
    model_id character varying(200) NOT NULL,
    provider_name character varying(200) NOT NULL,
    input_cost_per_1k numeric(18,8),
    output_cost_per_1k numeric(18,8),
    cache_read_cost_per_1m numeric(18,8),
    cache_write_5m_cost_per_1m numeric(18,8),
    cache_write_1h_cost_per_1m numeric(18,8),
    pricing_token_unit integer,
    tariff_state character varying(16) NOT NULL,
    pricing_policy_version character varying(32) NOT NULL,
    source_id integer,
    job_id integer,
    agent_task_id integer,
    agent_session_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_llm_dispatch_receipts_identity CHECK (((dispatch_id)::text ~ '^[0-9a-f]{32}$'::text)),
    CONSTRAINT ck_llm_dispatch_receipts_rates CHECK ((((input_cost_per_1k IS NULL) OR ((input_cost_per_1k >= (0)::numeric) AND (input_cost_per_1k <= 9999999999.99999999))) AND ((output_cost_per_1k IS NULL) OR ((output_cost_per_1k >= (0)::numeric) AND (output_cost_per_1k <= 9999999999.99999999))) AND ((cache_read_cost_per_1m IS NULL) OR ((cache_read_cost_per_1m >= (0)::numeric) AND (cache_read_cost_per_1m <= 9999999999.99999999))) AND ((cache_write_5m_cost_per_1m IS NULL) OR ((cache_write_5m_cost_per_1m >= (0)::numeric) AND (cache_write_5m_cost_per_1m <= 9999999999.99999999))) AND ((cache_write_1h_cost_per_1m IS NULL) OR ((cache_write_1h_cost_per_1m >= (0)::numeric) AND (cache_write_1h_cost_per_1m <= 9999999999.99999999))))),
    CONSTRAINT ck_llm_dispatch_receipts_tariff_snapshot CHECK (COALESCE(((((pricing_policy_version)::text = 'provider-price-v1'::text) AND ((((tariff_state)::text = 'undefined'::text) AND ((input_cost_per_1k IS NULL) OR (output_cost_per_1k IS NULL))) OR (((tariff_state)::text = 'free'::text) AND (input_cost_per_1k IS NOT NULL) AND (output_cost_per_1k IS NOT NULL) AND (input_cost_per_1k = (0)::numeric) AND (output_cost_per_1k = (0)::numeric)) OR (((tariff_state)::text = 'known'::text) AND (input_cost_per_1k IS NOT NULL) AND (output_cost_per_1k IS NOT NULL) AND ((input_cost_per_1k + output_cost_per_1k) > (0)::numeric))) AND (cache_read_cost_per_1m IS NULL) AND (cache_write_5m_cost_per_1m IS NULL) AND (cache_write_1h_cost_per_1m IS NULL) AND ((pricing_token_unit IS NULL) OR (pricing_token_unit = 1000))) OR (((pricing_policy_version)::text = 'cache-tariff-v2'::text) AND (pricing_token_unit = ANY (ARRAY[1000, 1000000])) AND ((((tariff_state)::text = 'undefined'::text) AND ((input_cost_per_1k IS NULL) OR (output_cost_per_1k IS NULL))) OR (((tariff_state)::text = 'free'::text) AND (input_cost_per_1k IS NOT NULL) AND (output_cost_per_1k IS NOT NULL) AND (cache_read_cost_per_1m IS NOT NULL) AND (cache_write_5m_cost_per_1m IS NOT NULL) AND (cache_write_1h_cost_per_1m IS NOT NULL) AND (input_cost_per_1k = (0)::numeric) AND (output_cost_per_1k = (0)::numeric) AND (cache_read_cost_per_1m = (0)::numeric) AND (cache_write_5m_cost_per_1m = (0)::numeric) AND (cache_write_1h_cost_per_1m = (0)::numeric)) OR (((tariff_state)::text = 'known'::text) AND (input_cost_per_1k IS NOT NULL) AND (output_cost_per_1k IS NOT NULL) AND ((cache_read_cost_per_1m IS DISTINCT FROM (0)::numeric) OR (cache_write_5m_cost_per_1m IS DISTINCT FROM (0)::numeric) OR (cache_write_1h_cost_per_1m IS DISTINCT FROM (0)::numeric) OR (input_cost_per_1k > (0)::numeric) OR (output_cost_per_1k > (0)::numeric)))))), false)),
    CONSTRAINT ck_llm_dispatch_receipts_tariff_state CHECK (((tariff_state)::text = ANY ((ARRAY['known'::character varying, 'free'::character varying, 'undefined'::character varying])::text[]))),
    CONSTRAINT ck_llm_dispatch_receipts_utc_day CHECK ((utc_day = ((prepared_at AT TIME ZONE 'UTC'::text))::date))
);


--

-- Name: llm_dispatch_receipts_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.llm_dispatch_receipts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: llm_dispatch_receipts_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.llm_dispatch_receipts_id_seq OWNED BY {{schema}}.llm_dispatch_receipts.id;


--

-- Name: llm_models; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.llm_models (
    id integer NOT NULL,
    provider_id integer NOT NULL,
    owner_user_id integer NOT NULL,
    name character varying(100) NOT NULL,
    model_id character varying(100) NOT NULL,
    description text,
    model_type character varying(20) NOT NULL,
    custom_endpoint_path character varying(200),
    input_cost_per_1k double precision NOT NULL,
    output_cost_per_1k double precision NOT NULL,
    cache_read_cost_per_1m numeric(18,8),
    cache_write_5m_cost_per_1m numeric(18,8),
    cache_write_1h_cost_per_1m numeric(18,8),
    pricing_token_unit integer DEFAULT 1000000 NOT NULL,
    last_request_cost double precision,
    last_request_cost_at timestamp without time zone,
    last_used_at timestamp without time zone,
    last_success_at timestamp without time zone,
    last_error_at timestamp without time zone,
    use_count integer DEFAULT 0 NOT NULL,
    fail_count integer DEFAULT 0 NOT NULL,
    max_tokens integer NOT NULL,
    default_temperature double precision NOT NULL,
    is_active boolean,
    is_default boolean,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_llm_models_cache_rates CHECK ((((cache_read_cost_per_1m IS NULL) OR (cache_read_cost_per_1m >= (0)::numeric)) AND ((cache_write_5m_cost_per_1m IS NULL) OR (cache_write_5m_cost_per_1m >= (0)::numeric)) AND ((cache_write_1h_cost_per_1m IS NULL) OR (cache_write_1h_cost_per_1m >= (0)::numeric)))),
    CONSTRAINT ck_llm_models_pricing_token_unit CHECK ((pricing_token_unit = ANY (ARRAY[1000, 1000000])))
);


--

-- Name: COLUMN llm_models.custom_endpoint_path; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.llm_models.custom_endpoint_path IS 'Custom endpoint path for api_format=custom, e.g. /v1/systemone';


--

-- Name: COLUMN llm_models.last_request_cost; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.llm_models.last_request_cost IS 'Estimated cost of the last test request (USD)';


--

-- Name: COLUMN llm_models.last_request_cost_at; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.llm_models.last_request_cost_at IS 'Timestamp of the last computed estimated cost';


--

-- Name: COLUMN llm_models.last_used_at; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.llm_models.last_used_at IS 'Last time this model was invoked';


--

-- Name: COLUMN llm_models.last_success_at; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.llm_models.last_success_at IS 'Last successful invocation (no 5xx/timeout)';


--

-- Name: COLUMN llm_models.last_error_at; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.llm_models.last_error_at IS 'Last failed invocation (5xx/timeout/network)';


--

-- Name: llm_models_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.llm_models_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: llm_models_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.llm_models_id_seq OWNED BY {{schema}}.llm_models.id;


--

-- Name: llm_outcome_events; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.llm_outcome_events (
    id integer NOT NULL,
    tenant_id integer NOT NULL,
    event_id character varying(32) NOT NULL,
    dispatch_id character varying(32) NOT NULL,
    event_kind character varying(32) NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    dispatch_state character varying(32) NOT NULL,
    outcome character varying(32) NOT NULL,
    prompt_tokens integer,
    completion_tokens integer,
    cache_usage jsonb,
    provider_price_state character varying(16) NOT NULL,
    provider_cost_raw character varying(128),
    cost_usd numeric(18,9),
    cost_certainty character varying(32) NOT NULL,
    price_source character varying(32) NOT NULL,
    pricing_policy_version character varying(32) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_llm_outcome_events_amount CHECK (((cost_usd IS NULL) OR ((cost_usd >= (0)::numeric) AND (cost_usd <= 999999999.999999999)))),
    CONSTRAINT ck_llm_outcome_events_certainty CHECK (((((cost_certainty)::text = ANY ((ARRAY['undefined'::character varying, 'not_dispatched'::character varying])::text[])) AND (cost_usd IS NULL)) OR (((cost_certainty)::text = 'free'::text) AND (cost_usd IS NOT NULL) AND (cost_usd = (0)::numeric)) OR (((cost_certainty)::text = 'priced'::text) AND (cost_usd IS NOT NULL)))),
    CONSTRAINT ck_llm_outcome_events_identity CHECK ((((event_id)::text ~ '^[0-9a-f]{32}$'::text) AND ((dispatch_id)::text ~ '^[0-9a-f]{32}$'::text))),
    CONSTRAINT ck_llm_outcome_events_kind CHECK (((event_kind)::text = ANY ((ARRAY['observation'::character varying, 'reconciliation'::character varying])::text[]))),
    CONSTRAINT ck_llm_outcome_events_no_dispatch CHECK (((((dispatch_state)::text = 'not_dispatched'::text) AND ((cost_certainty)::text = 'not_dispatched'::text) AND ((price_source)::text = 'not_dispatched'::text) AND ((provider_price_state)::text = 'absent'::text) AND (prompt_tokens IS NULL) AND (completion_tokens IS NULL)) OR (((dispatch_state)::text <> 'not_dispatched'::text) AND ((cost_certainty)::text <> 'not_dispatched'::text) AND ((price_source)::text <> 'not_dispatched'::text)))),
    CONSTRAINT ck_llm_outcome_events_no_price CHECK ((((price_source)::text <> 'none'::text) OR ((cost_usd IS NULL) AND ((cost_certainty)::text = 'undefined'::text)))),
    CONSTRAINT ck_llm_outcome_events_outcome CHECK (((((dispatch_state)::text = 'not_dispatched'::text) AND ((outcome)::text = 'not_dispatched'::text)) OR (((dispatch_state)::text = 'possible'::text) AND ((outcome)::text = ANY ((ARRAY['failed_transport'::character varying, 'timeout'::character varying, 'cancelled'::character varying, 'unknown'::character varying])::text[]))) OR (((dispatch_state)::text = 'response_received'::text) AND ((outcome)::text = ANY ((ARRAY['succeeded'::character varying, 'failed_output'::character varying, 'failed_transport'::character varying, 'timeout'::character varying, 'cancelled'::character varying, 'unknown'::character varying])::text[]))))),
    CONSTRAINT ck_llm_outcome_events_raw_decimal CHECK (((provider_cost_raw IS NULL) OR ((provider_cost_raw)::text ~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'::text))),
    CONSTRAINT ck_llm_outcome_events_report CHECK (((((provider_price_state)::text = 'absent'::text) AND (provider_cost_raw IS NULL) AND ((price_source)::text = ANY ((ARRAY['receipt_tariff'::character varying, 'none'::character varying, 'not_dispatched'::character varying])::text[]))) OR (((provider_price_state)::text = 'resolved'::text) AND (provider_cost_raw IS NOT NULL) AND ((price_source)::text = 'provider'::text) AND ((cost_certainty)::text = ANY ((ARRAY['priced'::character varying, 'free'::character varying])::text[]))) OR (((provider_price_state)::text = 'unresolved'::text) AND ((price_source)::text = 'provider'::text) AND ((cost_certainty)::text = 'undefined'::text)))),
    CONSTRAINT ck_llm_outcome_events_states CHECK ((((provider_price_state)::text = ANY ((ARRAY['absent'::character varying, 'resolved'::character varying, 'unresolved'::character varying])::text[])) AND ((cost_certainty)::text = ANY ((ARRAY['priced'::character varying, 'free'::character varying, 'undefined'::character varying, 'not_dispatched'::character varying])::text[])) AND ((price_source)::text = ANY ((ARRAY['provider'::character varying, 'receipt_tariff'::character varying, 'none'::character varying, 'not_dispatched'::character varying])::text[])))),
    CONSTRAINT ck_llm_outcome_events_usage CHECK ((((prompt_tokens IS NULL) OR (prompt_tokens >= 0)) AND ((completion_tokens IS NULL) OR (completion_tokens >= 0))))
);


--

-- Name: llm_outcome_events_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.llm_outcome_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: llm_outcome_events_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.llm_outcome_events_id_seq OWNED BY {{schema}}.llm_outcome_events.id;


--

-- Name: llm_providers; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.llm_providers (
    id integer NOT NULL,
    owner_user_id integer NOT NULL,
    uid character varying(100) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    api_format character varying(20) NOT NULL,
    base_url character varying(500) NOT NULL,
    auth_header character varying(200),
    encrypted_api_key text,
    is_active boolean,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: llm_providers_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.llm_providers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: llm_providers_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.llm_providers_id_seq OWNED BY {{schema}}.llm_providers.id;


--

-- Name: login_attempts; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.login_attempts (
    id integer NOT NULL,
    scope character varying(16) NOT NULL,
    identifier character varying(255) NOT NULL,
    failed_count integer DEFAULT 0 NOT NULL,
    last_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    locked_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: login_attempts_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.login_attempts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: login_attempts_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.login_attempts_id_seq OWNED BY {{schema}}.login_attempts.id;


--

-- Name: model_types; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.model_types (
    id integer NOT NULL,
    resource_key character varying(100) NOT NULL,
    model_name character varying(100) NOT NULL,
    table_name character varying(100) NOT NULL,
    description text,
    is_managed boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: model_types_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.model_types_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: model_types_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.model_types_id_seq OWNED BY {{schema}}.model_types.id;


--

-- Name: notification_preferences; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.notification_preferences (
    user_id integer NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    connections boolean DEFAULT true NOT NULL,
    tasks boolean DEFAULT true NOT NULL,
    popup boolean DEFAULT false NOT NULL,
    quiet boolean DEFAULT false NOT NULL,
    start character varying(5) DEFAULT '22:00'::character varying NOT NULL,
    "end" character varying(5) DEFAULT '08:00'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_notification_preferences_end CHECK ((("end")::text ~ '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$'::text)),
    CONSTRAINT ck_notification_preferences_quiet_interval CHECK (((NOT quiet) OR ((start)::text <> ("end")::text))),
    CONSTRAINT ck_notification_preferences_start CHECK (((start)::text ~ '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$'::text))
);


--

-- Name: notification_receipts; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.notification_receipts (
    id integer NOT NULL,
    notification_id integer NOT NULL,
    user_id integer NOT NULL,
    read_at timestamp with time zone,
    dismissed_at timestamp with time zone,
    snoozed_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: notification_receipts_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.notification_receipts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: notification_receipts_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.notification_receipts_id_seq OWNED BY {{schema}}.notification_receipts.id;


--

-- Name: notifications; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.notifications (
    id integer NOT NULL,
    title character varying(200) NOT NULL,
    message text NOT NULL,
    notification_type {{schema}}.notification_type NOT NULL,
    severity character varying(20) DEFAULT 'info'::character varying NOT NULL,
    dedup_key character varying(200),
    expires_at timestamp with time zone,
    is_read boolean,
    related_entity_type character varying(50),
    related_entity_id integer,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_notifications_severity CHECK (((severity)::text = ANY ((ARRAY['info'::character varying, 'warning'::character varying, 'action_required'::character varying])::text[])))
);


--

-- Name: notifications_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.notifications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: notifications_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.notifications_id_seq OWNED BY {{schema}}.notifications.id;


--

-- Name: permissions; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.permissions (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    action_type {{schema}}.action_type NOT NULL,
    model_type_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: permissions_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.permissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: permissions_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.permissions_id_seq OWNED BY {{schema}}.permissions.id;


--

-- Name: personal_billing_accounts; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.personal_billing_accounts (
    id integer NOT NULL,
    user_id integer NOT NULL,
    daily_limit_usd numeric(18,9),
    is_active boolean DEFAULT true NOT NULL,
    configured_at timestamp with time zone,
    policy_version character varying(64),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_personal_billing_accounts_configuration CHECK ((((configured_at IS NULL) AND (daily_limit_usd IS NULL) AND (policy_version IS NULL)) OR ((configured_at IS NOT NULL) AND (daily_limit_usd IS NOT NULL) AND ((policy_version)::text = 'personal-daily-cap-v1'::text)))),
    CONSTRAINT ck_personal_billing_accounts_daily_limit CHECK (((daily_limit_usd IS NULL) OR ((daily_limit_usd >= (0)::numeric) AND (daily_limit_usd <= 999999999.999999999))))
);


--

-- Name: personal_billing_accounts_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.personal_billing_accounts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: personal_billing_accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.personal_billing_accounts_id_seq OWNED BY {{schema}}.personal_billing_accounts.id;


--

-- Name: personal_billing_cutovers; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.personal_billing_cutovers (
    id integer NOT NULL,
    account_id integer NOT NULL,
    effective_at timestamp with time zone NOT NULL,
    reviewed_by_user_id integer NOT NULL,
    review_version character varying(32) DEFAULT 'clean-midnight-v1'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_personal_billing_cutovers_utc_midnight CHECK ((effective_at = (date_trunc('day'::text, (effective_at AT TIME ZONE 'UTC'::text)) AT TIME ZONE 'UTC'::text)))
);


--

-- Name: personal_billing_cutovers_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.personal_billing_cutovers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: personal_billing_cutovers_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.personal_billing_cutovers_id_seq OWNED BY {{schema}}.personal_billing_cutovers.id;


--

-- Name: personal_legacy_attribution_consents; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.personal_legacy_attribution_consents (
    id integer NOT NULL,
    source_key character varying(64) NOT NULL,
    dispatch_id character varying(32),
    attempt_key character varying(64),
    tenant_id integer NOT NULL,
    account_id integer NOT NULL,
    consented_by_user_id integer NOT NULL,
    consented_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_personal_legacy_consent_one_identity CHECK (((dispatch_id IS NOT NULL) <> (attempt_key IS NOT NULL))),
    CONSTRAINT ck_personal_legacy_consent_source_key CHECK ((((dispatch_id IS NOT NULL) AND ((source_key)::text = ('r:'::text || (dispatch_id)::text))) OR ((attempt_key IS NOT NULL) AND ((source_key)::text = ('a:'::text || (attempt_key)::text)))))
);


--

-- Name: personal_legacy_attribution_consents_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.personal_legacy_attribution_consents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: personal_legacy_attribution_consents_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.personal_legacy_attribution_consents_id_seq OWNED BY {{schema}}.personal_legacy_attribution_consents.id;


--

-- Name: personal_legacy_attributions; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.personal_legacy_attributions (
    id integer NOT NULL,
    source_key character varying(64) NOT NULL,
    dispatch_id character varying(32),
    attempt_key character varying(64),
    tenant_id integer NOT NULL,
    account_id integer NOT NULL,
    consent_id integer NOT NULL,
    consented_by_user_id integer NOT NULL,
    reviewed_by_user_id integer NOT NULL,
    reviewed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_personal_legacy_attribution_one_identity CHECK (((dispatch_id IS NOT NULL) <> (attempt_key IS NOT NULL))),
    CONSTRAINT ck_personal_legacy_attribution_source_key CHECK ((((dispatch_id IS NOT NULL) AND ((source_key)::text = ('r:'::text || (dispatch_id)::text))) OR ((attempt_key IS NOT NULL) AND ((source_key)::text = ('a:'::text || (attempt_key)::text)))))
);


--

-- Name: personal_legacy_attributions_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.personal_legacy_attributions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: personal_legacy_attributions_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.personal_legacy_attributions_id_seq OWNED BY {{schema}}.personal_legacy_attributions.id;


--

-- Name: personal_llm_billing_incidents; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.personal_llm_billing_incidents (
    id integer NOT NULL,
    event_id character varying(32) NOT NULL,
    dispatch_id character varying(32) NOT NULL,
    account_id integer NOT NULL,
    utc_day date NOT NULL,
    incident_kind character varying(32) NOT NULL,
    reserved_usd numeric(18,9) NOT NULL,
    actual_usd numeric(18,9),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_personal_llm_billing_incident_amounts CHECK ((((actual_usd IS NULL) OR ((actual_usd >= (0)::numeric) AND (actual_usd <= 999999999.999999999))) AND (reserved_usd >= (0)::numeric) AND (reserved_usd <= 999999999.999999999))),
    CONSTRAINT ck_personal_llm_billing_incident_kind CHECK (((incident_kind)::text = ANY ((ARRAY['bound_breach'::character varying, 'cap_overrun'::character varying, 'bound_and_cap_overrun'::character varying, 'terminal_conflict'::character varying])::text[])))
);


--

-- Name: personal_llm_billing_incidents_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.personal_llm_billing_incidents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: personal_llm_billing_incidents_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.personal_llm_billing_incidents_id_seq OWNED BY {{schema}}.personal_llm_billing_incidents.id;


--

-- Name: personal_llm_bound_policies; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.personal_llm_bound_policies (
    id integer NOT NULL,
    provider_row_id integer NOT NULL,
    model_row_id integer NOT NULL,
    provider_name character varying(200) NOT NULL,
    model_id character varying(200) NOT NULL,
    version character varying(64) NOT NULL,
    max_input_tokens integer NOT NULL,
    max_output_tokens integer NOT NULL,
    request_mode character varying(20) NOT NULL,
    reviewed_by_user_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_personal_llm_bound_policy_mode CHECK (((request_mode)::text = ANY ((ARRAY['text_only'::character varying, 'text_and_tools'::character varying])::text[]))),
    CONSTRAINT ck_personal_llm_bound_policy_tokens CHECK (((max_input_tokens > 0) AND (max_output_tokens > 0)))
);


--

-- Name: personal_llm_bound_policies_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.personal_llm_bound_policies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: personal_llm_bound_policies_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.personal_llm_bound_policies_id_seq OWNED BY {{schema}}.personal_llm_bound_policies.id;


--

-- Name: personal_llm_budget_days; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.personal_llm_budget_days (
    id integer NOT NULL,
    account_id integer NOT NULL,
    utc_day date NOT NULL,
    settled_usd numeric(18,9) DEFAULT '0'::numeric NOT NULL,
    reserved_usd numeric(18,9) DEFAULT '0'::numeric NOT NULL,
    undefined_count integer DEFAULT 0 NOT NULL,
    unbounded_count integer DEFAULT 0 NOT NULL,
    unresolved_count integer DEFAULT 0 NOT NULL,
    frozen boolean DEFAULT false NOT NULL,
    baseline_version character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_personal_llm_budget_day_amounts CHECK (((settled_usd >= (0)::numeric) AND (settled_usd <= 999999999.999999999) AND (reserved_usd >= (0)::numeric) AND (reserved_usd <= 999999999.999999999))),
    CONSTRAINT ck_personal_llm_budget_day_counts CHECK (((undefined_count >= 0) AND (unbounded_count >= 0) AND (unresolved_count >= 0)))
);


--

-- Name: personal_llm_budget_days_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.personal_llm_budget_days_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: personal_llm_budget_days_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.personal_llm_budget_days_id_seq OWNED BY {{schema}}.personal_llm_budget_days.id;


--

-- Name: personal_llm_dispatches; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.personal_llm_dispatches (
    id integer NOT NULL,
    dispatch_id character varying(32) NOT NULL,
    account_id integer NOT NULL,
    payer_user_id integer NOT NULL,
    initiated_by_user_id integer,
    tenant_id integer NOT NULL,
    purpose character varying(32) NOT NULL,
    stage character varying(32) NOT NULL,
    prepared_at timestamp with time zone NOT NULL,
    utc_day date NOT NULL,
    llm_model_row_id integer NOT NULL,
    llm_provider_row_id integer NOT NULL,
    model_id character varying(200) NOT NULL,
    provider_name character varying(200) NOT NULL,
    input_cost_per_1k numeric(18,8),
    output_cost_per_1k numeric(18,8),
    cache_read_cost_per_1m numeric(18,8),
    cache_write_5m_cost_per_1m numeric(18,8),
    cache_write_1h_cost_per_1m numeric(18,8),
    pricing_token_unit integer,
    tariff_state character varying(16) NOT NULL,
    pricing_policy_version character varying(32) NOT NULL,
    source_id integer,
    job_id integer,
    agent_task_id integer,
    agent_session_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_personal_llm_dispatch_identity CHECK (((dispatch_id)::text ~ '^[0-9a-f]{32}$'::text)),
    CONSTRAINT ck_personal_llm_dispatch_rates CHECK ((((input_cost_per_1k IS NULL) OR ((input_cost_per_1k >= (0)::numeric) AND (input_cost_per_1k <= 9999999999.99999999))) AND ((output_cost_per_1k IS NULL) OR ((output_cost_per_1k >= (0)::numeric) AND (output_cost_per_1k <= 9999999999.99999999))) AND ((cache_read_cost_per_1m IS NULL) OR ((cache_read_cost_per_1m >= (0)::numeric) AND (cache_read_cost_per_1m <= 9999999999.99999999))) AND ((cache_write_5m_cost_per_1m IS NULL) OR ((cache_write_5m_cost_per_1m >= (0)::numeric) AND (cache_write_5m_cost_per_1m <= 9999999999.99999999))) AND ((cache_write_1h_cost_per_1m IS NULL) OR ((cache_write_1h_cost_per_1m >= (0)::numeric) AND (cache_write_1h_cost_per_1m <= 9999999999.99999999))))),
    CONSTRAINT ck_personal_llm_dispatch_snapshot CHECK (COALESCE(((((pricing_policy_version)::text = 'provider-price-v1'::text) AND ((pricing_token_unit IS NULL) OR (pricing_token_unit = 1000)) AND (cache_read_cost_per_1m IS NULL) AND (cache_write_5m_cost_per_1m IS NULL) AND (cache_write_1h_cost_per_1m IS NULL) AND ((((tariff_state)::text = 'undefined'::text) AND ((input_cost_per_1k IS NULL) OR (output_cost_per_1k IS NULL))) OR (((tariff_state)::text = 'free'::text) AND (input_cost_per_1k IS NOT NULL) AND (output_cost_per_1k IS NOT NULL) AND (input_cost_per_1k = (0)::numeric) AND (output_cost_per_1k = (0)::numeric)) OR (((tariff_state)::text = 'known'::text) AND (input_cost_per_1k IS NOT NULL) AND (output_cost_per_1k IS NOT NULL) AND ((input_cost_per_1k + output_cost_per_1k) > (0)::numeric)))) OR (((pricing_policy_version)::text = 'cache-tariff-v2'::text) AND (pricing_token_unit = ANY (ARRAY[1000, 1000000])) AND ((((tariff_state)::text = 'undefined'::text) AND ((input_cost_per_1k IS NULL) OR (output_cost_per_1k IS NULL))) OR (((tariff_state)::text = 'free'::text) AND (input_cost_per_1k IS NOT NULL) AND (output_cost_per_1k IS NOT NULL) AND (cache_read_cost_per_1m IS NOT NULL) AND (cache_write_5m_cost_per_1m IS NOT NULL) AND (cache_write_1h_cost_per_1m IS NOT NULL) AND (input_cost_per_1k = (0)::numeric) AND (output_cost_per_1k = (0)::numeric) AND (cache_read_cost_per_1m = (0)::numeric) AND (cache_write_5m_cost_per_1m = (0)::numeric) AND (cache_write_1h_cost_per_1m = (0)::numeric)) OR (((tariff_state)::text = 'known'::text) AND (input_cost_per_1k IS NOT NULL) AND (output_cost_per_1k IS NOT NULL) AND ((cache_read_cost_per_1m IS DISTINCT FROM (0)::numeric) OR (cache_write_5m_cost_per_1m IS DISTINCT FROM (0)::numeric) OR (cache_write_1h_cost_per_1m IS DISTINCT FROM (0)::numeric) OR (input_cost_per_1k > (0)::numeric) OR (output_cost_per_1k > (0)::numeric)))))), false)),
    CONSTRAINT ck_personal_llm_dispatch_tariff CHECK (((tariff_state)::text = ANY ((ARRAY['known'::character varying, 'free'::character varying, 'undefined'::character varying])::text[]))),
    CONSTRAINT ck_personal_llm_dispatch_utc_day CHECK ((utc_day = ((prepared_at AT TIME ZONE 'UTC'::text))::date))
);


--

-- Name: personal_llm_dispatches_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.personal_llm_dispatches_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: personal_llm_dispatches_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.personal_llm_dispatches_id_seq OWNED BY {{schema}}.personal_llm_dispatches.id;


--

-- Name: personal_llm_ledger_entries; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.personal_llm_ledger_entries (
    id integer NOT NULL,
    entry_key character varying(32) NOT NULL,
    dispatch_id character varying(32) NOT NULL,
    account_id integer NOT NULL,
    utc_day date NOT NULL,
    event_id character varying(32),
    entry_kind character varying(16) NOT NULL,
    reserved_delta_usd numeric(18,9) NOT NULL,
    settled_delta_usd numeric(18,9) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_personal_llm_ledger_entry_deltas CHECK ((((reserved_delta_usd >= '-999999999.999999999'::numeric) AND (reserved_delta_usd <= 999999999.999999999)) AND ((settled_delta_usd >= '-999999999.999999999'::numeric) AND (settled_delta_usd <= 999999999.999999999)))),
    CONSTRAINT ck_personal_llm_ledger_entry_key CHECK (((entry_key)::text ~ '^[0-9a-f]{32}$'::text)),
    CONSTRAINT ck_personal_llm_ledger_entry_kind CHECK (((entry_kind)::text = ANY ((ARRAY['reserve'::character varying, 'observation'::character varying, 'settlement'::character varying, 'release'::character varying])::text[])))
);


--

-- Name: personal_llm_ledger_entries_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.personal_llm_ledger_entries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: personal_llm_ledger_entries_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.personal_llm_ledger_entries_id_seq OWNED BY {{schema}}.personal_llm_ledger_entries.id;


--

-- Name: personal_llm_reservation_states; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.personal_llm_reservation_states (
    id integer NOT NULL,
    dispatch_id character varying(32) NOT NULL,
    account_id integer NOT NULL,
    utc_day date NOT NULL,
    status character varying(16) NOT NULL,
    reserved_usd numeric(18,9) NOT NULL,
    settled_usd numeric(18,9) NOT NULL,
    latest_observed_at timestamp with time zone,
    latest_event_id character varying(32),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_personal_llm_reservation_states_amounts CHECK (((settled_usd >= (0)::numeric) AND (settled_usd <= 999999999.999999999) AND (reserved_usd >= (0)::numeric) AND (reserved_usd <= 999999999.999999999))),
    CONSTRAINT ck_personal_llm_reservation_states_status CHECK (((status)::text = ANY ((ARRAY['reserved'::character varying, 'settled'::character varying, 'released'::character varying])::text[])))
);


--

-- Name: personal_llm_reservation_states_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.personal_llm_reservation_states_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: personal_llm_reservation_states_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.personal_llm_reservation_states_id_seq OWNED BY {{schema}}.personal_llm_reservation_states.id;


--

-- Name: personal_llm_reservations; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.personal_llm_reservations (
    id integer NOT NULL,
    dispatch_id character varying(32) NOT NULL,
    account_id integer NOT NULL,
    utc_day date NOT NULL,
    ceiling_state character varying(16) NOT NULL,
    ceiling_usd numeric(18,9),
    bound_version character varying(64),
    undefined_policy_version character varying(32) NOT NULL,
    cap_usd numeric(18,9),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_personal_llm_reservations_amounts CHECK ((((ceiling_usd IS NULL) OR ((ceiling_usd >= (0)::numeric) AND (ceiling_usd <= 999999999.999999999))) AND ((cap_usd IS NULL) OR ((cap_usd > (0)::numeric) AND (cap_usd <= 999999999.999999999))))),
    CONSTRAINT ck_personal_llm_reservations_bound CHECK (((((ceiling_state)::text = 'bounded'::text) AND (ceiling_usd IS NOT NULL) AND ((ceiling_usd = (0)::numeric) OR (bound_version IS NOT NULL))) OR (((ceiling_state)::text = 'unbounded'::text) AND (ceiling_usd IS NULL) AND (bound_version IS NULL) AND (cap_usd IS NULL)) OR (((ceiling_state)::text = 'undefined'::text) AND (ceiling_usd = (0)::numeric) AND (bound_version IS NULL)))),
    CONSTRAINT ck_personal_llm_reservations_state CHECK (((ceiling_state)::text = ANY ((ARRAY['bounded'::character varying, 'unbounded'::character varying, 'undefined'::character varying])::text[]))),
    CONSTRAINT ck_personal_llm_reservations_undefined_policy CHECK (((undefined_policy_version)::text = 'owner-undefined-allow-v1'::text))
);


--

-- Name: personal_llm_reservations_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.personal_llm_reservations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: personal_llm_reservations_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.personal_llm_reservations_id_seq OWNED BY {{schema}}.personal_llm_reservations.id;


--

-- Name: platforms; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.platforms (
    id integer NOT NULL,
    name character varying(50) NOT NULL,
    platform_type {{schema}}.platform_type NOT NULL,
    base_url character varying(255) NOT NULL,
    params json NOT NULL,
    is_active boolean DEFAULT true
);


--

-- Name: platforms_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.platforms_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: platforms_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.platforms_id_seq OWNED BY {{schema}}.platforms.id;


--

-- Name: role_permission; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.role_permission (
    role_id integer NOT NULL,
    permission_id integer NOT NULL
);


--

-- Name: roles; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.roles (
    id integer NOT NULL,
    codename character varying(32) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_platform_role_codename CHECK (((codename)::text = ANY ((ARRAY['user'::character varying, 'operator'::character varying, 'admin'::character varying, 'superuser'::character varying])::text[])))
);


--

-- Name: roles_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: roles_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.roles_id_seq OWNED BY {{schema}}.roles.id;


--

-- Name: sources; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.sources (
    id integer NOT NULL,
    platform_id integer NOT NULL,
    name character varying(255) NOT NULL,
    source_type {{schema}}.source_type NOT NULL,
    external_id character varying(100) NOT NULL,
    params json NOT NULL,
    is_active boolean NOT NULL,
    last_checked timestamp with time zone,
    last_item_id character varying(100),
    owner_task_id integer,
    tenant_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: COLUMN sources.last_item_id; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.sources.last_item_id IS 'Watermark of the newest ingested item for push-based sources (Telegram Bot API)';


--

-- Name: COLUMN sources.owner_task_id; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.sources.owner_task_id IS 'Task-scoped query lane; hidden from the global source list';


--

-- Name: sources_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.sources_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: sources_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.sources_id_seq OWNED BY {{schema}}.sources.id;


--

-- Name: task_billing_payers; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.task_billing_payers (
    id integer NOT NULL,
    agent_task_id integer NOT NULL,
    tenant_id integer NOT NULL,
    payer_user_id integer NOT NULL,
    consented_by_user_id integer NOT NULL,
    consented_at timestamp with time zone DEFAULT now() NOT NULL,
    consent_version integer NOT NULL,
    CONSTRAINT ck_task_billing_payers_identity CHECK (((agent_task_id > 0) AND (consent_version > 0))),
    CONSTRAINT ck_task_billing_payers_self_consent CHECK ((consented_by_user_id = payer_user_id))
);


--

-- Name: task_billing_payers_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.task_billing_payers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: task_billing_payers_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.task_billing_payers_id_seq OWNED BY {{schema}}.task_billing_payers.id;


--

-- Name: tenant_channels; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.tenant_channels (
    id integer NOT NULL,
    tenant_id integer NOT NULL,
    channel character varying(20) NOT NULL,
    chat_id character varying(100) NOT NULL,
    kind character varying(20) DEFAULT 'private'::character varying NOT NULL,
    is_digest_target boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: tenant_channels_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.tenant_channels_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: tenant_channels_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.tenant_channels_id_seq OWNED BY {{schema}}.tenant_channels.id;


--

-- Name: tenant_invites; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.tenant_invites (
    id integer NOT NULL,
    tenant_id integer NOT NULL,
    code_hash character varying(64) NOT NULL,
    role_id integer,
    expires_at timestamp with time zone,
    max_uses integer DEFAULT 1 NOT NULL,
    used_count integer DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: COLUMN tenant_invites.role_id; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.tenant_invites.role_id IS 'Workspace role to assign on invite redemption';


--

-- Name: tenant_invites_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.tenant_invites_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: tenant_invites_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.tenant_invites_id_seq OWNED BY {{schema}}.tenant_invites.id;


--

-- Name: tenant_users; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.tenant_users (
    id integer NOT NULL,
    tenant_id integer NOT NULL,
    channel character varying(20) NOT NULL,
    external_user_id character varying(100) NOT NULL,
    user_id integer,
    role_id integer,
    ai_chat_enabled boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: COLUMN tenant_users.role_id; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.tenant_users.role_id IS 'Workspace role for this workspace membership (NULL = legacy/unresolved)';


--

-- Name: tenant_users_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.tenant_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: tenant_users_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.tenant_users_id_seq OWNED BY {{schema}}.tenant_users.id;


--

-- Name: tenants; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.tenants (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    slug character varying(50) NOT NULL,
    plan character varying(20) DEFAULT 'pro'::character varying NOT NULL,
    plan_expires_at timestamp with time zone,
    timezone character varying(50) DEFAULT 'Europe/Moscow'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    daily_cost_limit double precision DEFAULT '5'::double precision NOT NULL,
    max_sources integer DEFAULT 20 NOT NULL,
    agent_style json,
    agent_model_id integer,
    agent_model character varying(100),
    agent_max_tokens integer DEFAULT 1024,
    agent_temperature double precision DEFAULT '0.3'::double precision,
    agent_system_prompt text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_tenants_plan CHECK (((plan)::text = ANY ((ARRAY['starter'::character varying, 'pro'::character varying, 'business'::character varying])::text[])))
);


--

-- Name: COLUMN tenants.agent_style; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.tenants.agent_style IS 'Owner''s reply style contract: {tone, length, language, quiet_hours}; rendered into the system prompt';


--

-- Name: COLUMN tenants.agent_model; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.tenants.agent_model IS 'LLM model name override for agent chat (e.g. ''gpt-4o'', ''claude-3-sonnet'')';


--

-- Name: COLUMN tenants.agent_max_tokens; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.tenants.agent_max_tokens IS 'Max tokens for agent replies (overrides AGENT_MAX_TOKENS)';


--

-- Name: COLUMN tenants.agent_temperature; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.tenants.agent_temperature IS 'Temperature for agent replies (overrides AGENT_TEMPERATURE)';


--

-- Name: COLUMN tenants.agent_system_prompt; Type: COMMENT; Schema: {{schema}}; Owner: -
--

COMMENT ON COLUMN {{schema}}.tenants.agent_system_prompt IS 'Custom system prompt override (appended to built-in DEFAULT_SYSTEM_PROMPT)';


--

-- Name: tenants_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.tenants_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: tenants_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.tenants_id_seq OWNED BY {{schema}}.tenants.id;


--

-- Name: user_credentials; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.user_credentials (
    id integer NOT NULL,
    user_id integer NOT NULL,
    platform character varying(30) NOT NULL,
    kind character varying(30) NOT NULL,
    label character varying(100),
    secret_encrypted text NOT NULL,
    expires_at timestamp with time zone,
    meta json,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: user_credentials_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.user_credentials_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: user_credentials_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.user_credentials_id_seq OWNED BY {{schema}}.user_credentials.id;


--

-- Name: users; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.users (
    id integer NOT NULL,
    username character varying(50) NOT NULL,
    email character varying(100) NOT NULL,
    hashed_password character varying(255) NOT NULL,
    is_active boolean NOT NULL,
    role_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--

-- Name: users_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.users_id_seq OWNED BY {{schema}}.users.id;


--

-- Name: workspace_role_permission; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.workspace_role_permission (
    role_id integer NOT NULL,
    permission_id integer NOT NULL
);


--

-- Name: workspace_roles; Type: TABLE; Schema: {{schema}}; Owner: -
--

CREATE TABLE {{schema}}.workspace_roles (
    id integer NOT NULL,
    codename character varying(32) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_workspace_role_codename CHECK (((codename)::text = ANY ((ARRAY['owner'::character varying, 'admin'::character varying, 'manager'::character varying, 'analyst'::character varying, 'viewer'::character varying])::text[])))
);


--

-- Name: workspace_roles_id_seq; Type: SEQUENCE; Schema: {{schema}}; Owner: -
--

CREATE SEQUENCE {{schema}}.workspace_roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--

-- Name: workspace_roles_id_seq; Type: SEQUENCE OWNED BY; Schema: {{schema}}; Owner: -
--

ALTER SEQUENCE {{schema}}.workspace_roles_id_seq OWNED BY {{schema}}.workspace_roles.id;


--

-- Name: action_audit_events id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.action_audit_events ALTER COLUMN id SET DEFAULT nextval('{{schema}}.action_audit_events_id_seq'::regclass);


--

-- Name: agent_feedback id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_feedback ALTER COLUMN id SET DEFAULT nextval('{{schema}}.agent_feedback_id_seq'::regclass);


--

-- Name: agent_memory id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_memory ALTER COLUMN id SET DEFAULT nextval('{{schema}}.agent_memory_id_seq'::regclass);


--

-- Name: agent_messages id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_messages ALTER COLUMN id SET DEFAULT nextval('{{schema}}.agent_messages_id_seq'::regclass);


--

-- Name: agent_scenarios id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_scenarios ALTER COLUMN id SET DEFAULT nextval('{{schema}}.agent_scenarios_id_seq'::regclass);


--

-- Name: agent_sessions id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_sessions ALTER COLUMN id SET DEFAULT nextval('{{schema}}.agent_sessions_id_seq'::regclass);


--

-- Name: agent_tasks id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_tasks ALTER COLUMN id SET DEFAULT nextval('{{schema}}.agent_tasks_id_seq'::regclass);


--

-- Name: ai_analytics id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.ai_analytics ALTER COLUMN id SET DEFAULT nextval('{{schema}}.ai_analytics_id_seq'::regclass);


--

-- Name: analyzed_content_receipts id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.analyzed_content_receipts ALTER COLUMN id SET DEFAULT nextval('{{schema}}.analyzed_content_receipts_id_seq'::regclass);


--

-- Name: bot_actions id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.bot_actions ALTER COLUMN id SET DEFAULT nextval('{{schema}}.bot_actions_id_seq'::regclass);


--

-- Name: collected_items id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collected_items ALTER COLUMN id SET DEFAULT nextval('{{schema}}.collected_items_id_seq'::regclass);


--

-- Name: collection_candidates id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_candidates ALTER COLUMN id SET DEFAULT nextval('{{schema}}.collection_candidates_id_seq'::regclass);


--

-- Name: collection_lane_runs id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_lane_runs ALTER COLUMN id SET DEFAULT nextval('{{schema}}.collection_lane_runs_id_seq'::regclass);


--

-- Name: collection_seen id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_seen ALTER COLUMN id SET DEFAULT nextval('{{schema}}.collection_seen_id_seq'::regclass);


--

-- Name: digest_runs id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.digest_runs ALTER COLUMN id SET DEFAULT nextval('{{schema}}.digest_runs_id_seq'::regclass);


--

-- Name: entitlement_usage id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.entitlement_usage ALTER COLUMN id SET DEFAULT nextval('{{schema}}.entitlement_usage_id_seq'::regclass);


--

-- Name: jobs id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.jobs ALTER COLUMN id SET DEFAULT nextval('{{schema}}.jobs_id_seq'::regclass);


--

-- Name: llm_attempts id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_attempts ALTER COLUMN id SET DEFAULT nextval('{{schema}}.llm_attempts_id_seq'::regclass);


--

-- Name: llm_budget_days id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_budget_days ALTER COLUMN id SET DEFAULT nextval('{{schema}}.llm_budget_days_id_seq'::regclass);


--

-- Name: llm_budget_reservations id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_budget_reservations ALTER COLUMN id SET DEFAULT nextval('{{schema}}.llm_budget_reservations_id_seq'::regclass);


--

-- Name: llm_dispatch_receipts id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_dispatch_receipts ALTER COLUMN id SET DEFAULT nextval('{{schema}}.llm_dispatch_receipts_id_seq'::regclass);


--

-- Name: llm_models id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_models ALTER COLUMN id SET DEFAULT nextval('{{schema}}.llm_models_id_seq'::regclass);


--

-- Name: llm_outcome_events id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_outcome_events ALTER COLUMN id SET DEFAULT nextval('{{schema}}.llm_outcome_events_id_seq'::regclass);


--

-- Name: llm_providers id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_providers ALTER COLUMN id SET DEFAULT nextval('{{schema}}.llm_providers_id_seq'::regclass);


--

-- Name: login_attempts id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.login_attempts ALTER COLUMN id SET DEFAULT nextval('{{schema}}.login_attempts_id_seq'::regclass);


--

-- Name: model_types id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.model_types ALTER COLUMN id SET DEFAULT nextval('{{schema}}.model_types_id_seq'::regclass);


--

-- Name: notification_receipts id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.notification_receipts ALTER COLUMN id SET DEFAULT nextval('{{schema}}.notification_receipts_id_seq'::regclass);


--

-- Name: notifications id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.notifications ALTER COLUMN id SET DEFAULT nextval('{{schema}}.notifications_id_seq'::regclass);


--

-- Name: permissions id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.permissions ALTER COLUMN id SET DEFAULT nextval('{{schema}}.permissions_id_seq'::regclass);


--

-- Name: personal_billing_accounts id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_billing_accounts ALTER COLUMN id SET DEFAULT nextval('{{schema}}.personal_billing_accounts_id_seq'::regclass);


--

-- Name: personal_billing_cutovers id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_billing_cutovers ALTER COLUMN id SET DEFAULT nextval('{{schema}}.personal_billing_cutovers_id_seq'::regclass);


--

-- Name: personal_legacy_attribution_consents id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attribution_consents ALTER COLUMN id SET DEFAULT nextval('{{schema}}.personal_legacy_attribution_consents_id_seq'::regclass);


--

-- Name: personal_legacy_attributions id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attributions ALTER COLUMN id SET DEFAULT nextval('{{schema}}.personal_legacy_attributions_id_seq'::regclass);


--

-- Name: personal_llm_billing_incidents id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_billing_incidents ALTER COLUMN id SET DEFAULT nextval('{{schema}}.personal_llm_billing_incidents_id_seq'::regclass);


--

-- Name: personal_llm_bound_policies id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_bound_policies ALTER COLUMN id SET DEFAULT nextval('{{schema}}.personal_llm_bound_policies_id_seq'::regclass);


--

-- Name: personal_llm_budget_days id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_budget_days ALTER COLUMN id SET DEFAULT nextval('{{schema}}.personal_llm_budget_days_id_seq'::regclass);


--

-- Name: personal_llm_dispatches id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_dispatches ALTER COLUMN id SET DEFAULT nextval('{{schema}}.personal_llm_dispatches_id_seq'::regclass);


--

-- Name: personal_llm_ledger_entries id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_ledger_entries ALTER COLUMN id SET DEFAULT nextval('{{schema}}.personal_llm_ledger_entries_id_seq'::regclass);


--

-- Name: personal_llm_reservation_states id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservation_states ALTER COLUMN id SET DEFAULT nextval('{{schema}}.personal_llm_reservation_states_id_seq'::regclass);


--

-- Name: personal_llm_reservations id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservations ALTER COLUMN id SET DEFAULT nextval('{{schema}}.personal_llm_reservations_id_seq'::regclass);


--

-- Name: platforms id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.platforms ALTER COLUMN id SET DEFAULT nextval('{{schema}}.platforms_id_seq'::regclass);


--

-- Name: roles id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.roles ALTER COLUMN id SET DEFAULT nextval('{{schema}}.roles_id_seq'::regclass);


--

-- Name: sources id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.sources ALTER COLUMN id SET DEFAULT nextval('{{schema}}.sources_id_seq'::regclass);


--

-- Name: task_billing_payers id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.task_billing_payers ALTER COLUMN id SET DEFAULT nextval('{{schema}}.task_billing_payers_id_seq'::regclass);


--

-- Name: tenant_channels id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_channels ALTER COLUMN id SET DEFAULT nextval('{{schema}}.tenant_channels_id_seq'::regclass);


--

-- Name: tenant_invites id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_invites ALTER COLUMN id SET DEFAULT nextval('{{schema}}.tenant_invites_id_seq'::regclass);


--

-- Name: tenant_users id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_users ALTER COLUMN id SET DEFAULT nextval('{{schema}}.tenant_users_id_seq'::regclass);


--

-- Name: tenants id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenants ALTER COLUMN id SET DEFAULT nextval('{{schema}}.tenants_id_seq'::regclass);


--

-- Name: user_credentials id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.user_credentials ALTER COLUMN id SET DEFAULT nextval('{{schema}}.user_credentials_id_seq'::regclass);


--

-- Name: users id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.users ALTER COLUMN id SET DEFAULT nextval('{{schema}}.users_id_seq'::regclass);


--

-- Name: workspace_roles id; Type: DEFAULT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.workspace_roles ALTER COLUMN id SET DEFAULT nextval('{{schema}}.workspace_roles_id_seq'::regclass);


--

-- Name: action_audit_events action_audit_events_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.action_audit_events
    ADD CONSTRAINT action_audit_events_pkey PRIMARY KEY (id);


--

-- Name: agent_feedback agent_feedback_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_feedback
    ADD CONSTRAINT agent_feedback_pkey PRIMARY KEY (id);


--

-- Name: agent_memory agent_memory_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_memory
    ADD CONSTRAINT agent_memory_pkey PRIMARY KEY (id);


--

-- Name: agent_messages agent_messages_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_messages
    ADD CONSTRAINT agent_messages_pkey PRIMARY KEY (id);


--

-- Name: agent_scenarios agent_scenarios_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_scenarios
    ADD CONSTRAINT agent_scenarios_pkey PRIMARY KEY (id);


--

-- Name: agent_sessions agent_sessions_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_sessions
    ADD CONSTRAINT agent_sessions_pkey PRIMARY KEY (id);


--

-- Name: agent_task_sources agent_task_sources_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_task_sources
    ADD CONSTRAINT agent_task_sources_pkey PRIMARY KEY (agent_task_id, source_id);


--

-- Name: agent_tasks agent_tasks_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_tasks
    ADD CONSTRAINT agent_tasks_pkey PRIMARY KEY (id);


--

-- Name: ai_analytics ai_analytics_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.ai_analytics
    ADD CONSTRAINT ai_analytics_pkey PRIMARY KEY (id);


--

-- Name: analyzed_content_receipts analyzed_content_receipts_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.analyzed_content_receipts
    ADD CONSTRAINT analyzed_content_receipts_pkey PRIMARY KEY (id);


--

-- Name: bot_actions bot_actions_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.bot_actions
    ADD CONSTRAINT bot_actions_pkey PRIMARY KEY (id);


--

-- Name: collected_items collected_items_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collected_items
    ADD CONSTRAINT collected_items_pkey PRIMARY KEY (id);


--

-- Name: collection_candidates collection_candidates_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_candidates
    ADD CONSTRAINT collection_candidates_pkey PRIMARY KEY (id);


--

-- Name: collection_lane_runs collection_lane_runs_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_lane_runs
    ADD CONSTRAINT collection_lane_runs_pkey PRIMARY KEY (id);


--

-- Name: collection_seen collection_seen_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_seen
    ADD CONSTRAINT collection_seen_pkey PRIMARY KEY (id);


--

-- Name: digest_runs digest_runs_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.digest_runs
    ADD CONSTRAINT digest_runs_pkey PRIMARY KEY (id);


--

-- Name: entitlement_usage entitlement_usage_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.entitlement_usage
    ADD CONSTRAINT entitlement_usage_pkey PRIMARY KEY (id);


--

-- Name: jobs jobs_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (id);


--

-- Name: llm_attempts llm_attempts_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_attempts
    ADD CONSTRAINT llm_attempts_pkey PRIMARY KEY (id);


--

-- Name: llm_budget_days llm_budget_days_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_budget_days
    ADD CONSTRAINT llm_budget_days_pkey PRIMARY KEY (id);


--

-- Name: llm_budget_reservations llm_budget_reservations_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_budget_reservations
    ADD CONSTRAINT llm_budget_reservations_pkey PRIMARY KEY (id);


--

-- Name: llm_dispatch_receipts llm_dispatch_receipts_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_dispatch_receipts
    ADD CONSTRAINT llm_dispatch_receipts_pkey PRIMARY KEY (id);


--

-- Name: llm_models llm_models_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_models
    ADD CONSTRAINT llm_models_pkey PRIMARY KEY (id);


--

-- Name: llm_outcome_events llm_outcome_events_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_outcome_events
    ADD CONSTRAINT llm_outcome_events_pkey PRIMARY KEY (id);


--

-- Name: llm_providers llm_providers_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_providers
    ADD CONSTRAINT llm_providers_pkey PRIMARY KEY (id);


--

-- Name: login_attempts login_attempts_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.login_attempts
    ADD CONSTRAINT login_attempts_pkey PRIMARY KEY (id);


--

-- Name: model_types model_types_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.model_types
    ADD CONSTRAINT model_types_pkey PRIMARY KEY (id);


--

-- Name: model_types model_types_resource_key_key; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.model_types
    ADD CONSTRAINT model_types_resource_key_key UNIQUE (resource_key);


--

-- Name: notification_preferences notification_preferences_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.notification_preferences
    ADD CONSTRAINT notification_preferences_pkey PRIMARY KEY (user_id);


--

-- Name: notification_receipts notification_receipts_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.notification_receipts
    ADD CONSTRAINT notification_receipts_pkey PRIMARY KEY (id);


--

-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--

-- Name: permissions permissions_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.permissions
    ADD CONSTRAINT permissions_pkey PRIMARY KEY (id);


--

-- Name: personal_billing_accounts personal_billing_accounts_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_billing_accounts
    ADD CONSTRAINT personal_billing_accounts_pkey PRIMARY KEY (id);


--

-- Name: personal_billing_cutovers personal_billing_cutovers_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_billing_cutovers
    ADD CONSTRAINT personal_billing_cutovers_pkey PRIMARY KEY (id);


--

-- Name: personal_legacy_attribution_consents personal_legacy_attribution_consents_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attribution_consents
    ADD CONSTRAINT personal_legacy_attribution_consents_pkey PRIMARY KEY (id);


--

-- Name: personal_legacy_attributions personal_legacy_attributions_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attributions
    ADD CONSTRAINT personal_legacy_attributions_pkey PRIMARY KEY (id);


--

-- Name: personal_llm_billing_incidents personal_llm_billing_incidents_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_billing_incidents
    ADD CONSTRAINT personal_llm_billing_incidents_pkey PRIMARY KEY (id);


--

-- Name: personal_llm_bound_policies personal_llm_bound_policies_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_bound_policies
    ADD CONSTRAINT personal_llm_bound_policies_pkey PRIMARY KEY (id);


--

-- Name: personal_llm_budget_days personal_llm_budget_days_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_budget_days
    ADD CONSTRAINT personal_llm_budget_days_pkey PRIMARY KEY (id);


--

-- Name: personal_llm_dispatches personal_llm_dispatches_dispatch_id_key; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_dispatches
    ADD CONSTRAINT personal_llm_dispatches_dispatch_id_key UNIQUE (dispatch_id);


--

-- Name: personal_llm_dispatches personal_llm_dispatches_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_dispatches
    ADD CONSTRAINT personal_llm_dispatches_pkey PRIMARY KEY (id);


--

-- Name: personal_llm_ledger_entries personal_llm_ledger_entries_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_ledger_entries
    ADD CONSTRAINT personal_llm_ledger_entries_pkey PRIMARY KEY (id);


--

-- Name: personal_llm_reservation_states personal_llm_reservation_states_dispatch_id_key; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservation_states
    ADD CONSTRAINT personal_llm_reservation_states_dispatch_id_key UNIQUE (dispatch_id);


--

-- Name: personal_llm_reservation_states personal_llm_reservation_states_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservation_states
    ADD CONSTRAINT personal_llm_reservation_states_pkey PRIMARY KEY (id);


--

-- Name: personal_llm_reservations personal_llm_reservations_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservations
    ADD CONSTRAINT personal_llm_reservations_pkey PRIMARY KEY (id);


--

-- Name: platforms platforms_name_key; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.platforms
    ADD CONSTRAINT platforms_name_key UNIQUE (name);


--

-- Name: platforms platforms_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.platforms
    ADD CONSTRAINT platforms_pkey PRIMARY KEY (id);


--

-- Name: role_permission role_permission_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.role_permission
    ADD CONSTRAINT role_permission_pkey PRIMARY KEY (role_id, permission_id);


--

-- Name: roles roles_codename_key; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.roles
    ADD CONSTRAINT roles_codename_key UNIQUE (codename);


--

-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--

-- Name: sources sources_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.sources
    ADD CONSTRAINT sources_pkey PRIMARY KEY (id);


--

-- Name: task_billing_payers task_billing_payers_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.task_billing_payers
    ADD CONSTRAINT task_billing_payers_pkey PRIMARY KEY (id);


--

-- Name: tenant_channels tenant_channels_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_channels
    ADD CONSTRAINT tenant_channels_pkey PRIMARY KEY (id);


--

-- Name: tenant_invites tenant_invites_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_invites
    ADD CONSTRAINT tenant_invites_pkey PRIMARY KEY (id);


--

-- Name: tenant_users tenant_users_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_users
    ADD CONSTRAINT tenant_users_pkey PRIMARY KEY (id);


--

-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);


--

-- Name: action_audit_events uq_action_audit_events_event_id; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.action_audit_events
    ADD CONSTRAINT uq_action_audit_events_event_id UNIQUE (event_id);


--

-- Name: agent_sessions uq_agent_session_tenant_channel_chat; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_sessions
    ADD CONSTRAINT uq_agent_session_tenant_channel_chat UNIQUE (tenant_id, channel, chat_id);


--

-- Name: agent_tasks uq_agent_task_tenant_launch_key; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_tasks
    ADD CONSTRAINT uq_agent_task_tenant_launch_key UNIQUE (tenant_id, launch_key);


--

-- Name: agent_tasks uq_agent_task_tenant_name; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_tasks
    ADD CONSTRAINT uq_agent_task_tenant_name UNIQUE (tenant_id, name);


--

-- Name: ai_analytics uq_analytics_source_date_period_context; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.ai_analytics
    ADD CONSTRAINT uq_analytics_source_date_period_context UNIQUE (source_id, analysis_date, period_type, analysis_fingerprint);


--

-- Name: analyzed_content_receipts uq_analyzed_content_receipt_identity; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.analyzed_content_receipts
    ADD CONSTRAINT uq_analyzed_content_receipt_identity UNIQUE (tenant_id, source_id, content_hash, analysis_fingerprint);


--

-- Name: bot_actions uq_bot_actions_tenant_idempotency; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.bot_actions
    ADD CONSTRAINT uq_bot_actions_tenant_idempotency UNIQUE (tenant_id, idempotency_key);


--

-- Name: collection_candidates uq_collection_candidates_identity; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_candidates
    ADD CONSTRAINT uq_collection_candidates_identity UNIQUE (tenant_id, platform, kind, external_id);


--

-- Name: collection_seen uq_collection_seen_identity; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_seen
    ADD CONSTRAINT uq_collection_seen_identity UNIQUE (tenant_id, source_id, content_type, external_id);


--

-- Name: digest_runs uq_digest_agent_task_period; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.digest_runs
    ADD CONSTRAINT uq_digest_agent_task_period UNIQUE (agent_task_id, period_start, period_end);


--

-- Name: entitlement_usage uq_entitlement_usage_job_capability; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.entitlement_usage
    ADD CONSTRAINT uq_entitlement_usage_job_capability UNIQUE (tenant_id, job_id, capability);


--

-- Name: llm_attempts uq_llm_attempts_key; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_attempts
    ADD CONSTRAINT uq_llm_attempts_key UNIQUE (attempt_key);


--

-- Name: llm_budget_days uq_llm_budget_days_payer_day; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_budget_days
    ADD CONSTRAINT uq_llm_budget_days_payer_day UNIQUE (tenant_id, utc_day);


--

-- Name: llm_budget_reservations uq_llm_budget_reservations_identity; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_budget_reservations
    ADD CONSTRAINT uq_llm_budget_reservations_identity UNIQUE (dispatch_id);


--

-- Name: llm_dispatch_receipts uq_llm_dispatch_receipts_identity; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_dispatch_receipts
    ADD CONSTRAINT uq_llm_dispatch_receipts_identity UNIQUE (dispatch_id);


--

-- Name: llm_outcome_events uq_llm_outcome_events_identity; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_outcome_events
    ADD CONSTRAINT uq_llm_outcome_events_identity UNIQUE (event_id);


--

-- Name: llm_providers uq_llm_provider_id_owner; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_providers
    ADD CONSTRAINT uq_llm_provider_id_owner UNIQUE (id, owner_user_id);


--

-- Name: llm_providers uq_llm_provider_owner_uid; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_providers
    ADD CONSTRAINT uq_llm_provider_owner_uid UNIQUE (owner_user_id, uid);


--

-- Name: login_attempts uq_login_attempts_scope_identifier; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.login_attempts
    ADD CONSTRAINT uq_login_attempts_scope_identifier UNIQUE (scope, identifier);


--

-- Name: notification_receipts uq_notification_receipt_owner; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.notification_receipts
    ADD CONSTRAINT uq_notification_receipt_owner UNIQUE (notification_id, user_id);


--

-- Name: permissions uq_permission_resource_action; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.permissions
    ADD CONSTRAINT uq_permission_resource_action UNIQUE (model_type_id, action_type);


--

-- Name: personal_billing_accounts uq_personal_billing_accounts_user; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_billing_accounts
    ADD CONSTRAINT uq_personal_billing_accounts_user UNIQUE (user_id);


--

-- Name: personal_billing_cutovers uq_personal_billing_cutovers_account; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_billing_cutovers
    ADD CONSTRAINT uq_personal_billing_cutovers_account UNIQUE (account_id);


--

-- Name: personal_legacy_attributions uq_personal_legacy_attribution_consent; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attributions
    ADD CONSTRAINT uq_personal_legacy_attribution_consent UNIQUE (consent_id);


--

-- Name: personal_legacy_attributions uq_personal_legacy_attribution_identity; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attributions
    ADD CONSTRAINT uq_personal_legacy_attribution_identity UNIQUE (source_key);


--

-- Name: personal_legacy_attribution_consents uq_personal_legacy_consent_identity_account; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attribution_consents
    ADD CONSTRAINT uq_personal_legacy_consent_identity_account UNIQUE (source_key, account_id);


--

-- Name: personal_llm_billing_incidents uq_personal_llm_billing_incident_event; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_billing_incidents
    ADD CONSTRAINT uq_personal_llm_billing_incident_event UNIQUE (event_id);


--

-- Name: personal_llm_bound_policies uq_personal_llm_bound_policy_version; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_bound_policies
    ADD CONSTRAINT uq_personal_llm_bound_policy_version UNIQUE (provider_row_id, model_row_id, version);


--

-- Name: personal_llm_budget_days uq_personal_llm_budget_day_account_day; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_budget_days
    ADD CONSTRAINT uq_personal_llm_budget_day_account_day UNIQUE (account_id, utc_day);


--

-- Name: personal_llm_ledger_entries uq_personal_llm_ledger_entry_key; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_ledger_entries
    ADD CONSTRAINT uq_personal_llm_ledger_entry_key UNIQUE (entry_key);


--

-- Name: personal_llm_ledger_entries uq_personal_llm_ledger_event_kind; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_ledger_entries
    ADD CONSTRAINT uq_personal_llm_ledger_event_kind UNIQUE (event_id, entry_kind);


--

-- Name: personal_llm_reservations uq_personal_llm_reservations_dispatch; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservations
    ADD CONSTRAINT uq_personal_llm_reservations_dispatch UNIQUE (dispatch_id);


--

-- Name: personal_llm_reservations uq_personal_llm_reservations_dispatch_account_day; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservations
    ADD CONSTRAINT uq_personal_llm_reservations_dispatch_account_day UNIQUE (dispatch_id, account_id, utc_day);


--

-- Name: sources uq_source_tenant_platform_external; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.sources
    ADD CONSTRAINT uq_source_tenant_platform_external UNIQUE (tenant_id, platform_id, external_id);


--

-- Name: task_billing_payers uq_task_billing_payers_consent; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.task_billing_payers
    ADD CONSTRAINT uq_task_billing_payers_consent UNIQUE (agent_task_id, consent_version);


--

-- Name: tenant_channels uq_tenant_channel_chat; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_channels
    ADD CONSTRAINT uq_tenant_channel_chat UNIQUE (channel, chat_id);


--

-- Name: tenant_invites uq_tenant_invite_code_hash; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_invites
    ADD CONSTRAINT uq_tenant_invite_code_hash UNIQUE (code_hash);


--

-- Name: tenants uq_tenant_slug; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenants
    ADD CONSTRAINT uq_tenant_slug UNIQUE (slug);


--

-- Name: tenant_users uq_tenant_user_identity; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_users
    ADD CONSTRAINT uq_tenant_user_identity UNIQUE (tenant_id, channel, external_user_id);


--

-- Name: user_credentials user_credentials_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.user_credentials
    ADD CONSTRAINT user_credentials_pkey PRIMARY KEY (id);


--

-- Name: users users_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--

-- Name: workspace_role_permission workspace_role_permission_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.workspace_role_permission
    ADD CONSTRAINT workspace_role_permission_pkey PRIMARY KEY (role_id, permission_id);


--

-- Name: workspace_roles workspace_roles_codename_key; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.workspace_roles
    ADD CONSTRAINT workspace_roles_codename_key UNIQUE (codename);


--

-- Name: workspace_roles workspace_roles_pkey; Type: CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.workspace_roles
    ADD CONSTRAINT workspace_roles_pkey PRIMARY KEY (id);


--

-- Name: idx_agent_tasks_next_run_at; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX idx_agent_tasks_next_run_at ON {{schema}}.agent_tasks USING btree (next_run_at);


--

-- Name: idx_ai_analytics_date; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX idx_ai_analytics_date ON {{schema}}.ai_analytics USING btree (analysis_date);


--

-- Name: idx_ai_analytics_source; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX idx_ai_analytics_source ON {{schema}}.ai_analytics USING btree (source_id);


--

-- Name: idx_ai_analytics_source_content_hash; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX idx_ai_analytics_source_content_hash ON {{schema}}.ai_analytics USING btree (source_id, content_hash);


--

-- Name: idx_ai_analytics_topic_chain; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX idx_ai_analytics_topic_chain ON {{schema}}.ai_analytics USING btree (topic_chain_id);


--

-- Name: idx_jobs_agent_task_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX idx_jobs_agent_task_id ON {{schema}}.jobs USING btree (agent_task_id);


--

-- Name: idx_jobs_status_run_at; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX idx_jobs_status_run_at ON {{schema}}.jobs USING btree (status, run_at);


--

-- Name: idx_llm_attempts_job; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX idx_llm_attempts_job ON {{schema}}.llm_attempts USING btree (job_id);


--

-- Name: idx_llm_attempts_source_date; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX idx_llm_attempts_source_date ON {{schema}}.llm_attempts USING btree (source_id, analysis_date);


--

-- Name: idx_sources_external_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX idx_sources_external_id ON {{schema}}.sources USING btree (external_id);


--

-- Name: idx_sources_last_checked; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX idx_sources_last_checked ON {{schema}}.sources USING btree (last_checked);


--

-- Name: idx_sources_platform_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX idx_sources_platform_id ON {{schema}}.sources USING btree (platform_id);


--

-- Name: ix_action_audit_events_tenant_outcome_time; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_action_audit_events_tenant_outcome_time ON {{schema}}.action_audit_events USING btree (tenant_id, outcome, occurred_at);


--

-- Name: ix_action_audit_events_tenant_time_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_action_audit_events_tenant_time_id ON {{schema}}.action_audit_events USING btree (tenant_id, occurred_at, id);


--

-- Name: ix_agent_feedback_session_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_agent_feedback_session_id ON {{schema}}.agent_feedback USING btree (session_id);


--

-- Name: ix_agent_feedback_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_agent_feedback_tenant_id ON {{schema}}.agent_feedback USING btree (tenant_id);


--

-- Name: ix_agent_feedback_vote; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_agent_feedback_vote ON {{schema}}.agent_feedback USING btree (vote);


--

-- Name: ix_agent_memory_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_agent_memory_tenant_id ON {{schema}}.agent_memory USING btree (tenant_id);


--

-- Name: ix_agent_messages_session_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_agent_messages_session_id ON {{schema}}.agent_messages USING btree (session_id);


--

-- Name: ix_agent_messages_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_agent_messages_tenant_id ON {{schema}}.agent_messages USING btree (tenant_id);


--

-- Name: ix_agent_scenarios_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_agent_scenarios_tenant_id ON {{schema}}.agent_scenarios USING btree (tenant_id);


--

-- Name: ix_agent_sessions_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_agent_sessions_tenant_id ON {{schema}}.agent_sessions USING btree (tenant_id);


--

-- Name: ix_agent_tasks_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_agent_tasks_tenant_id ON {{schema}}.agent_tasks USING btree (tenant_id);


--

-- Name: ix_ai_analytics_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_ai_analytics_tenant_id ON {{schema}}.ai_analytics USING btree (tenant_id);


--

-- Name: ix_analyzed_content_receipts_source; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_analyzed_content_receipts_source ON {{schema}}.analyzed_content_receipts USING btree (tenant_id, source_id);


--

-- Name: ix_bot_actions_agent_scenario_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_bot_actions_agent_scenario_id ON {{schema}}.bot_actions USING btree (agent_scenario_id);


--

-- Name: ix_bot_actions_agent_task_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_bot_actions_agent_task_id ON {{schema}}.bot_actions USING btree (agent_task_id);


--

-- Name: ix_bot_actions_source_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_bot_actions_source_id ON {{schema}}.bot_actions USING btree (source_id);


--

-- Name: ix_bot_actions_status; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_bot_actions_status ON {{schema}}.bot_actions USING btree (status);


--

-- Name: ix_bot_actions_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_bot_actions_tenant_id ON {{schema}}.bot_actions USING btree (tenant_id);


--

-- Name: ix_collected_items_run_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_collected_items_run_id ON {{schema}}.collected_items USING btree (run_id);


--

-- Name: ix_collected_items_source_hash; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_collected_items_source_hash ON {{schema}}.collected_items USING btree (source_id, content_hash);


--

-- Name: ix_collected_items_source_published; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_collected_items_source_published ON {{schema}}.collected_items USING btree (source_id, published_at);


--

-- Name: ix_collected_items_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_collected_items_tenant_id ON {{schema}}.collected_items USING btree (tenant_id);


--

-- Name: ix_collected_items_tenant_selection; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_collected_items_tenant_selection ON {{schema}}.collected_items USING btree (tenant_id, selection_status);


--

-- Name: ix_collection_candidates_tenant_status; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_collection_candidates_tenant_status ON {{schema}}.collection_candidates USING btree (tenant_id, status);


--

-- Name: ix_collection_lane_runs_tenant_job; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_collection_lane_runs_tenant_job ON {{schema}}.collection_lane_runs USING btree (tenant_id, job_id);


--

-- Name: ix_collection_lane_runs_tenant_task; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_collection_lane_runs_tenant_task ON {{schema}}.collection_lane_runs USING btree (tenant_id, task_id);


--

-- Name: ix_collection_seen_tenant_source; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_collection_seen_tenant_source ON {{schema}}.collection_seen USING btree (tenant_id, source_id);


--

-- Name: ix_digest_runs_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_digest_runs_tenant_id ON {{schema}}.digest_runs USING btree (tenant_id);


--

-- Name: ix_entitlement_usage_daily; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_entitlement_usage_daily ON {{schema}}.entitlement_usage USING btree (tenant_id, capability, admitted_at);


--

-- Name: ix_jobs_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_jobs_tenant_id ON {{schema}}.jobs USING btree (tenant_id);


--

-- Name: ix_llm_attempts_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_llm_attempts_tenant_id ON {{schema}}.llm_attempts USING btree (tenant_id);


--

-- Name: ix_llm_dispatch_receipts_tenant_day; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_llm_dispatch_receipts_tenant_day ON {{schema}}.llm_dispatch_receipts USING btree (tenant_id, utc_day);


--

-- Name: ix_llm_outcome_events_tenant_receipt; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_llm_outcome_events_tenant_receipt ON {{schema}}.llm_outcome_events USING btree (tenant_id, dispatch_id, observed_at);


--

-- Name: ix_notification_receipts_user_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_notification_receipts_user_id ON {{schema}}.notification_receipts USING btree (user_id);


--

-- Name: ix_notifications_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_notifications_tenant_id ON {{schema}}.notifications USING btree (tenant_id);


--

-- Name: ix_personal_llm_dispatches_account_created; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_personal_llm_dispatches_account_created ON {{schema}}.personal_llm_dispatches USING btree (account_id, created_at);


--

-- Name: ix_personal_llm_dispatches_workspace; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_personal_llm_dispatches_workspace ON {{schema}}.personal_llm_dispatches USING btree (tenant_id);


--

-- Name: ix_sources_owner_task_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_sources_owner_task_id ON {{schema}}.sources USING btree (owner_task_id);


--

-- Name: ix_sources_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_sources_tenant_id ON {{schema}}.sources USING btree (tenant_id);


--

-- Name: ix_task_billing_payers_current; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_task_billing_payers_current ON {{schema}}.task_billing_payers USING btree (tenant_id, agent_task_id, consent_version);


--

-- Name: ix_tenant_channels_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_tenant_channels_tenant_id ON {{schema}}.tenant_channels USING btree (tenant_id);


--

-- Name: ix_tenant_invites_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_tenant_invites_tenant_id ON {{schema}}.tenant_invites USING btree (tenant_id);


--

-- Name: ix_tenant_users_tenant_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_tenant_users_tenant_id ON {{schema}}.tenant_users USING btree (tenant_id);


--

-- Name: ix_tenant_users_user_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_tenant_users_user_id ON {{schema}}.tenant_users USING btree (user_id);


--

-- Name: ix_tenants_slug; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_tenants_slug ON {{schema}}.tenants USING btree (slug);


--

-- Name: ix_test_schema_llm_models_owner_user_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_test_schema_llm_models_owner_user_id ON {{schema}}.llm_models USING btree (owner_user_id);


--

-- Name: ix_test_schema_llm_models_provider_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_test_schema_llm_models_provider_id ON {{schema}}.llm_models USING btree (provider_id);


--

-- Name: ix_test_schema_llm_providers_owner_user_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_test_schema_llm_providers_owner_user_id ON {{schema}}.llm_providers USING btree (owner_user_id);


--

-- Name: ix_test_schema_users_email; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE UNIQUE INDEX ix_test_schema_users_email ON {{schema}}.users USING btree (email);


--

-- Name: ix_test_schema_users_username; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE UNIQUE INDEX ix_test_schema_users_username ON {{schema}}.users USING btree (username);


--

-- Name: ix_user_credentials_user_id; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_user_credentials_user_id ON {{schema}}.user_credentials USING btree (user_id);


--

-- Name: ix_user_credentials_user_platform; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE INDEX ix_user_credentials_user_platform ON {{schema}}.user_credentials USING btree (user_id, platform);


--

-- Name: uq_agent_memory_tenant_scope_key_legacy; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE UNIQUE INDEX uq_agent_memory_tenant_scope_key_legacy ON {{schema}}.agent_memory USING btree (tenant_id, scope, key) WHERE (user_id IS NULL);


--

-- Name: uq_agent_memory_tenant_user_scope_key; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE UNIQUE INDEX uq_agent_memory_tenant_user_scope_key ON {{schema}}.agent_memory USING btree (tenant_id, user_id, scope, key) WHERE (user_id IS NOT NULL);


--

-- Name: uq_collected_items_source_content_external; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE UNIQUE INDEX uq_collected_items_source_content_external ON {{schema}}.collected_items USING btree (source_id, content_type, external_id);


--

-- Name: uq_notifications_tenant_dedup; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE UNIQUE INDEX uq_notifications_tenant_dedup ON {{schema}}.notifications USING btree (tenant_id, dedup_key) WHERE (dedup_key IS NOT NULL);


--

-- Name: uq_tenant_users_web_membership; Type: INDEX; Schema: {{schema}}; Owner: -
--

CREATE UNIQUE INDEX uq_tenant_users_web_membership ON {{schema}}.tenant_users USING btree (tenant_id, user_id) WHERE (user_id IS NOT NULL);


--

-- Name: action_audit_events action_audit_events_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER action_audit_events_immutable BEFORE DELETE OR UPDATE ON {{schema}}.action_audit_events FOR EACH ROW EXECUTE FUNCTION {{schema}}.guard_action_audit_mutation();


--

-- Name: action_audit_events action_audit_events_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER action_audit_events_no_truncate BEFORE TRUNCATE ON {{schema}}.action_audit_events FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.guard_action_audit_mutation();


--

-- Name: llm_models guard_personal_catalog_owner; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER guard_personal_catalog_owner BEFORE UPDATE ON {{schema}}.llm_models FOR EACH ROW EXECUTE FUNCTION {{schema}}.guard_personal_catalog_owner();


--

-- Name: llm_providers guard_personal_catalog_owner; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER guard_personal_catalog_owner BEFORE UPDATE ON {{schema}}.llm_providers FOR EACH ROW EXECUTE FUNCTION {{schema}}.guard_personal_catalog_owner();


--

-- Name: agent_tasks guard_task_scenario_binding; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER guard_task_scenario_binding BEFORE INSERT OR UPDATE ON {{schema}}.agent_tasks FOR EACH ROW EXECUTE FUNCTION {{schema}}.guard_task_binding();


--

-- Name: agent_task_sources guard_task_source_binding; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER guard_task_source_binding BEFORE INSERT OR UPDATE ON {{schema}}.agent_task_sources FOR EACH ROW EXECUTE FUNCTION {{schema}}.guard_task_binding();


--

-- Name: agent_scenarios guard_workspace_ownership; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER guard_workspace_ownership BEFORE UPDATE OF tenant_id ON {{schema}}.agent_scenarios FOR EACH ROW EXECUTE FUNCTION {{schema}}.guard_task_binding();


--

-- Name: sources guard_workspace_ownership; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER guard_workspace_ownership BEFORE INSERT OR UPDATE OF tenant_id, owner_task_id ON {{schema}}.sources FOR EACH ROW EXECUTE FUNCTION {{schema}}.guard_task_binding();


--

-- Name: llm_budget_reservations llm_budget_reservations_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER llm_budget_reservations_immutable BEFORE DELETE OR UPDATE ON {{schema}}.llm_budget_reservations FOR EACH ROW EXECUTE FUNCTION {{schema}}.reject_llm_budget_reservation_mutation();


--

-- Name: llm_budget_reservations llm_budget_reservations_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER llm_budget_reservations_no_truncate BEFORE TRUNCATE ON {{schema}}.llm_budget_reservations FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.reject_llm_budget_reservation_mutation();


--

-- Name: llm_budget_reservations llm_budget_reservations_receipt_binding; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER llm_budget_reservations_receipt_binding BEFORE INSERT ON {{schema}}.llm_budget_reservations FOR EACH ROW EXECUTE FUNCTION {{schema}}.check_llm_budget_reservation_receipt();


--

-- Name: llm_dispatch_receipts llm_dispatch_receipts_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER llm_dispatch_receipts_immutable BEFORE DELETE OR UPDATE ON {{schema}}.llm_dispatch_receipts FOR EACH ROW EXECUTE FUNCTION {{schema}}.reject_llm_receipt_mutation();


--

-- Name: llm_dispatch_receipts llm_dispatch_receipts_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER llm_dispatch_receipts_no_truncate BEFORE TRUNCATE ON {{schema}}.llm_dispatch_receipts FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.reject_llm_receipt_mutation();


--

-- Name: llm_outcome_events llm_outcome_events_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER llm_outcome_events_immutable BEFORE DELETE OR UPDATE ON {{schema}}.llm_outcome_events FOR EACH ROW EXECUTE FUNCTION {{schema}}.reject_llm_outcome_mutation();


--

-- Name: llm_outcome_events llm_outcome_events_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER llm_outcome_events_no_truncate BEFORE TRUNCATE ON {{schema}}.llm_outcome_events FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.reject_llm_outcome_mutation();


--

-- Name: llm_outcome_events llm_outcome_events_receipt_binding; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER llm_outcome_events_receipt_binding BEFORE INSERT ON {{schema}}.llm_outcome_events FOR EACH ROW EXECUTE FUNCTION {{schema}}.check_llm_outcome_receipt();


--

-- Name: personal_billing_cutovers personal_billing_cutovers_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_billing_cutovers_immutable BEFORE DELETE OR UPDATE ON {{schema}}.personal_billing_cutovers FOR EACH ROW EXECUTE FUNCTION {{schema}}.reject_personal_billing_cutovers_mutation();


--

-- Name: personal_billing_cutovers personal_billing_cutovers_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_billing_cutovers_no_truncate BEFORE TRUNCATE ON {{schema}}.personal_billing_cutovers FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.reject_personal_billing_cutovers_mutation();


--

-- Name: personal_billing_cutovers personal_billing_cutovers_validate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_billing_cutovers_validate BEFORE INSERT ON {{schema}}.personal_billing_cutovers FOR EACH ROW EXECUTE FUNCTION {{schema}}.check_personal_billing_cutover();


--

-- Name: personal_legacy_attribution_consents personal_legacy_attribution_consents_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_legacy_attribution_consents_immutable BEFORE DELETE OR UPDATE ON {{schema}}.personal_legacy_attribution_consents FOR EACH ROW EXECUTE FUNCTION {{schema}}.reject_personal_legacy_attribution_consents_mutation();


--

-- Name: personal_legacy_attribution_consents personal_legacy_attribution_consents_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_legacy_attribution_consents_no_truncate BEFORE TRUNCATE ON {{schema}}.personal_legacy_attribution_consents FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.reject_personal_legacy_attribution_consents_mutation();


--

-- Name: personal_legacy_attribution_consents personal_legacy_attribution_consents_validate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_legacy_attribution_consents_validate BEFORE INSERT ON {{schema}}.personal_legacy_attribution_consents FOR EACH ROW EXECUTE FUNCTION {{schema}}.check_personal_legacy_attribution_consent();


--

-- Name: personal_legacy_attributions personal_legacy_attributions_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_legacy_attributions_immutable BEFORE DELETE OR UPDATE ON {{schema}}.personal_legacy_attributions FOR EACH ROW EXECUTE FUNCTION {{schema}}.reject_personal_legacy_attributions_mutation();


--

-- Name: personal_legacy_attributions personal_legacy_attributions_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_legacy_attributions_no_truncate BEFORE TRUNCATE ON {{schema}}.personal_legacy_attributions FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.reject_personal_legacy_attributions_mutation();


--

-- Name: personal_legacy_attributions personal_legacy_attributions_validate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_legacy_attributions_validate BEFORE INSERT ON {{schema}}.personal_legacy_attributions FOR EACH ROW EXECUTE FUNCTION {{schema}}.check_personal_legacy_attribution_review();


--

-- Name: personal_llm_billing_incidents personal_llm_billing_incidents_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_billing_incidents_immutable BEFORE DELETE OR UPDATE ON {{schema}}.personal_llm_billing_incidents FOR EACH ROW EXECUTE FUNCTION {{schema}}.reject_personal_llm_billing_incidents_mutation();


--

-- Name: personal_llm_billing_incidents personal_llm_billing_incidents_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_billing_incidents_no_truncate BEFORE TRUNCATE ON {{schema}}.personal_llm_billing_incidents FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.reject_personal_llm_billing_incidents_mutation();


--

-- Name: personal_llm_billing_incidents personal_llm_billing_incidents_validate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_billing_incidents_validate BEFORE INSERT ON {{schema}}.personal_llm_billing_incidents FOR EACH ROW EXECUTE FUNCTION {{schema}}.check_personal_llm_billing_incident();


--

-- Name: personal_llm_bound_policies personal_llm_bound_policies_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_bound_policies_immutable BEFORE DELETE OR UPDATE ON {{schema}}.personal_llm_bound_policies FOR EACH ROW EXECUTE FUNCTION {{schema}}.reject_personal_llm_bound_policies_mutation();


--

-- Name: personal_llm_bound_policies personal_llm_bound_policies_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_bound_policies_no_truncate BEFORE TRUNCATE ON {{schema}}.personal_llm_bound_policies FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.reject_personal_llm_bound_policies_mutation();


--

-- Name: personal_llm_bound_policies personal_llm_bound_policies_validate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_bound_policies_validate BEFORE INSERT ON {{schema}}.personal_llm_bound_policies FOR EACH ROW EXECUTE FUNCTION {{schema}}.check_personal_llm_bound_policy();


--

-- Name: personal_llm_dispatches personal_llm_dispatches_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_dispatches_immutable BEFORE DELETE OR UPDATE ON {{schema}}.personal_llm_dispatches FOR EACH ROW EXECUTE FUNCTION {{schema}}.reject_personal_llm_dispatches_mutation();


--

-- Name: personal_llm_dispatches personal_llm_dispatches_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_dispatches_no_truncate BEFORE TRUNCATE ON {{schema}}.personal_llm_dispatches FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.reject_personal_llm_dispatches_mutation();


--

-- Name: personal_llm_dispatches personal_llm_dispatches_validate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_dispatches_validate BEFORE INSERT ON {{schema}}.personal_llm_dispatches FOR EACH ROW EXECUTE FUNCTION {{schema}}.check_personal_llm_dispatch_account();


--

-- Name: personal_llm_ledger_entries personal_llm_ledger_entries_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_ledger_entries_immutable BEFORE DELETE OR UPDATE ON {{schema}}.personal_llm_ledger_entries FOR EACH ROW EXECUTE FUNCTION {{schema}}.reject_personal_llm_ledger_entries_mutation();


--

-- Name: personal_llm_ledger_entries personal_llm_ledger_entries_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_ledger_entries_no_truncate BEFORE TRUNCATE ON {{schema}}.personal_llm_ledger_entries FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.reject_personal_llm_ledger_entries_mutation();


--

-- Name: personal_llm_ledger_entries personal_llm_ledger_entries_validate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_ledger_entries_validate BEFORE INSERT ON {{schema}}.personal_llm_ledger_entries FOR EACH ROW EXECUTE FUNCTION {{schema}}.check_personal_llm_ledger_entry();


--

-- Name: personal_llm_reservation_states personal_llm_reservation_states_event_validate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_reservation_states_event_validate BEFORE INSERT OR UPDATE ON {{schema}}.personal_llm_reservation_states FOR EACH ROW EXECUTE FUNCTION {{schema}}.check_personal_llm_reservation_state_event();


--

-- Name: personal_llm_reservations personal_llm_reservations_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_reservations_immutable BEFORE DELETE OR UPDATE ON {{schema}}.personal_llm_reservations FOR EACH ROW EXECUTE FUNCTION {{schema}}.reject_personal_llm_reservations_mutation();


--

-- Name: personal_llm_reservations personal_llm_reservations_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_reservations_no_truncate BEFORE TRUNCATE ON {{schema}}.personal_llm_reservations FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.reject_personal_llm_reservations_mutation();


--

-- Name: personal_llm_reservations personal_llm_reservations_validate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER personal_llm_reservations_validate BEFORE INSERT ON {{schema}}.personal_llm_reservations FOR EACH ROW EXECUTE FUNCTION {{schema}}.check_personal_llm_reservation();


--

-- Name: task_billing_payers task_billing_payers_immutable; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER task_billing_payers_immutable BEFORE DELETE OR UPDATE ON {{schema}}.task_billing_payers FOR EACH ROW EXECUTE FUNCTION {{schema}}.reject_task_billing_payers_mutation();


--

-- Name: task_billing_payers task_billing_payers_no_truncate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER task_billing_payers_no_truncate BEFORE TRUNCATE ON {{schema}}.task_billing_payers FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.reject_task_billing_payers_mutation();


--

-- Name: task_billing_payers task_billing_payers_validate; Type: TRIGGER; Schema: {{schema}}; Owner: -
--

CREATE TRIGGER task_billing_payers_validate BEFORE INSERT ON {{schema}}.task_billing_payers FOR EACH ROW EXECUTE FUNCTION {{schema}}.check_task_billing_payer_consent();


--

-- Name: agent_feedback agent_feedback_message_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_feedback
    ADD CONSTRAINT agent_feedback_message_id_fkey FOREIGN KEY (message_id) REFERENCES {{schema}}.agent_messages(id) ON DELETE SET NULL;


--

-- Name: agent_feedback agent_feedback_session_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_feedback
    ADD CONSTRAINT agent_feedback_session_id_fkey FOREIGN KEY (session_id) REFERENCES {{schema}}.agent_sessions(id) ON DELETE CASCADE;


--

-- Name: agent_feedback agent_feedback_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_feedback
    ADD CONSTRAINT agent_feedback_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: agent_memory agent_memory_evidence_message_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_memory
    ADD CONSTRAINT agent_memory_evidence_message_id_fkey FOREIGN KEY (evidence_message_id) REFERENCES {{schema}}.agent_messages(id) ON DELETE SET NULL;


--

-- Name: agent_memory agent_memory_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_memory
    ADD CONSTRAINT agent_memory_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: agent_memory agent_memory_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_memory
    ADD CONSTRAINT agent_memory_user_id_fkey FOREIGN KEY (user_id) REFERENCES {{schema}}.users(id) ON DELETE CASCADE;


--

-- Name: agent_messages agent_messages_session_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_messages
    ADD CONSTRAINT agent_messages_session_id_fkey FOREIGN KEY (session_id) REFERENCES {{schema}}.agent_sessions(id) ON DELETE CASCADE;


--

-- Name: agent_messages agent_messages_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_messages
    ADD CONSTRAINT agent_messages_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: agent_scenarios agent_scenarios_image_llm_model_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_scenarios
    ADD CONSTRAINT agent_scenarios_image_llm_model_id_fkey FOREIGN KEY (image_llm_model_id) REFERENCES {{schema}}.llm_models(id) ON DELETE SET NULL;


--

-- Name: agent_scenarios agent_scenarios_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_scenarios
    ADD CONSTRAINT agent_scenarios_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: agent_scenarios agent_scenarios_text_llm_model_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_scenarios
    ADD CONSTRAINT agent_scenarios_text_llm_model_id_fkey FOREIGN KEY (text_llm_model_id) REFERENCES {{schema}}.llm_models(id) ON DELETE SET NULL;


--

-- Name: agent_scenarios agent_scenarios_video_llm_model_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_scenarios
    ADD CONSTRAINT agent_scenarios_video_llm_model_id_fkey FOREIGN KEY (video_llm_model_id) REFERENCES {{schema}}.llm_models(id) ON DELETE SET NULL;


--

-- Name: agent_sessions agent_sessions_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_sessions
    ADD CONSTRAINT agent_sessions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: agent_task_sources agent_task_sources_agent_task_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_task_sources
    ADD CONSTRAINT agent_task_sources_agent_task_id_fkey FOREIGN KEY (agent_task_id) REFERENCES {{schema}}.agent_tasks(id) ON DELETE CASCADE;


--

-- Name: agent_task_sources agent_task_sources_source_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_task_sources
    ADD CONSTRAINT agent_task_sources_source_id_fkey FOREIGN KEY (source_id) REFERENCES {{schema}}.sources(id) ON DELETE CASCADE;


--

-- Name: agent_tasks agent_tasks_agent_scenario_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_tasks
    ADD CONSTRAINT agent_tasks_agent_scenario_id_fkey FOREIGN KEY (agent_scenario_id) REFERENCES {{schema}}.agent_scenarios(id) ON DELETE SET NULL;


--

-- Name: agent_tasks agent_tasks_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.agent_tasks
    ADD CONSTRAINT agent_tasks_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: ai_analytics ai_analytics_parent_analysis_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.ai_analytics
    ADD CONSTRAINT ai_analytics_parent_analysis_id_fkey FOREIGN KEY (parent_analysis_id) REFERENCES {{schema}}.ai_analytics(id) ON DELETE SET NULL;


--

-- Name: ai_analytics ai_analytics_source_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.ai_analytics
    ADD CONSTRAINT ai_analytics_source_id_fkey FOREIGN KEY (source_id) REFERENCES {{schema}}.sources(id) ON DELETE CASCADE;


--

-- Name: ai_analytics ai_analytics_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.ai_analytics
    ADD CONSTRAINT ai_analytics_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: analyzed_content_receipts analyzed_content_receipts_analytics_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.analyzed_content_receipts
    ADD CONSTRAINT analyzed_content_receipts_analytics_id_fkey FOREIGN KEY (analytics_id) REFERENCES {{schema}}.ai_analytics(id) ON DELETE SET NULL;


--

-- Name: analyzed_content_receipts analyzed_content_receipts_scenario_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.analyzed_content_receipts
    ADD CONSTRAINT analyzed_content_receipts_scenario_id_fkey FOREIGN KEY (scenario_id) REFERENCES {{schema}}.agent_scenarios(id) ON DELETE SET NULL;


--

-- Name: analyzed_content_receipts analyzed_content_receipts_source_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.analyzed_content_receipts
    ADD CONSTRAINT analyzed_content_receipts_source_id_fkey FOREIGN KEY (source_id) REFERENCES {{schema}}.sources(id) ON DELETE CASCADE;


--

-- Name: analyzed_content_receipts analyzed_content_receipts_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.analyzed_content_receipts
    ADD CONSTRAINT analyzed_content_receipts_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: bot_actions bot_actions_agent_scenario_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.bot_actions
    ADD CONSTRAINT bot_actions_agent_scenario_id_fkey FOREIGN KEY (agent_scenario_id) REFERENCES {{schema}}.agent_scenarios(id) ON DELETE SET NULL;


--

-- Name: bot_actions bot_actions_agent_task_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.bot_actions
    ADD CONSTRAINT bot_actions_agent_task_id_fkey FOREIGN KEY (agent_task_id) REFERENCES {{schema}}.agent_tasks(id) ON DELETE SET NULL;


--

-- Name: bot_actions bot_actions_analytics_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.bot_actions
    ADD CONSTRAINT bot_actions_analytics_id_fkey FOREIGN KEY (analytics_id) REFERENCES {{schema}}.ai_analytics(id) ON DELETE SET NULL;


--

-- Name: bot_actions bot_actions_approved_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.bot_actions
    ADD CONSTRAINT bot_actions_approved_user_id_fkey FOREIGN KEY (approved_user_id) REFERENCES {{schema}}.users(id) ON DELETE SET NULL;


--

-- Name: bot_actions bot_actions_confirmed_by_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.bot_actions
    ADD CONSTRAINT bot_actions_confirmed_by_fkey FOREIGN KEY (confirmed_by) REFERENCES {{schema}}.tenant_users(id) ON DELETE SET NULL;


--

-- Name: bot_actions bot_actions_source_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.bot_actions
    ADD CONSTRAINT bot_actions_source_id_fkey FOREIGN KEY (source_id) REFERENCES {{schema}}.sources(id) ON DELETE SET NULL;


--

-- Name: bot_actions bot_actions_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.bot_actions
    ADD CONSTRAINT bot_actions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: collected_items collected_items_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collected_items
    ADD CONSTRAINT collected_items_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: collection_candidates collection_candidates_source_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_candidates
    ADD CONSTRAINT collection_candidates_source_id_fkey FOREIGN KEY (source_id) REFERENCES {{schema}}.sources(id) ON DELETE SET NULL;


--

-- Name: collection_candidates collection_candidates_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_candidates
    ADD CONSTRAINT collection_candidates_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: collection_lane_runs collection_lane_runs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_lane_runs
    ADD CONSTRAINT collection_lane_runs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: collection_seen collection_seen_source_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_seen
    ADD CONSTRAINT collection_seen_source_id_fkey FOREIGN KEY (source_id) REFERENCES {{schema}}.sources(id) ON DELETE CASCADE;


--

-- Name: collection_seen collection_seen_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.collection_seen
    ADD CONSTRAINT collection_seen_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: digest_runs digest_runs_agent_task_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.digest_runs
    ADD CONSTRAINT digest_runs_agent_task_id_fkey FOREIGN KEY (agent_task_id) REFERENCES {{schema}}.agent_tasks(id) ON DELETE SET NULL;


--

-- Name: digest_runs digest_runs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.digest_runs
    ADD CONSTRAINT digest_runs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: entitlement_usage entitlement_usage_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.entitlement_usage
    ADD CONSTRAINT entitlement_usage_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: action_audit_events fk_action_audit_events_tenant; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.action_audit_events
    ADD CONSTRAINT fk_action_audit_events_tenant FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE RESTRICT;


--

-- Name: llm_budget_reservations fk_llm_budget_reservations_day; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_budget_reservations
    ADD CONSTRAINT fk_llm_budget_reservations_day FOREIGN KEY (tenant_id, utc_day) REFERENCES {{schema}}.llm_budget_days(tenant_id, utc_day) ON DELETE RESTRICT;


--

-- Name: llm_models fk_llm_model_provider_owner; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_models
    ADD CONSTRAINT fk_llm_model_provider_owner FOREIGN KEY (provider_id, owner_user_id) REFERENCES {{schema}}.llm_providers(id, owner_user_id) ON DELETE CASCADE;


--

-- Name: personal_llm_ledger_entries fk_personal_llm_ledger_entries_day; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_ledger_entries
    ADD CONSTRAINT fk_personal_llm_ledger_entries_day FOREIGN KEY (account_id, utc_day) REFERENCES {{schema}}.personal_llm_budget_days(account_id, utc_day) ON DELETE RESTRICT;


--

-- Name: personal_llm_reservation_states fk_personal_llm_reservation_states_day; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservation_states
    ADD CONSTRAINT fk_personal_llm_reservation_states_day FOREIGN KEY (account_id, utc_day) REFERENCES {{schema}}.personal_llm_budget_days(account_id, utc_day) ON DELETE RESTRICT;


--

-- Name: personal_llm_reservation_states fk_personal_llm_reservation_states_reservation; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservation_states
    ADD CONSTRAINT fk_personal_llm_reservation_states_reservation FOREIGN KEY (dispatch_id, account_id, utc_day) REFERENCES {{schema}}.personal_llm_reservations(dispatch_id, account_id, utc_day) ON DELETE RESTRICT;


--

-- Name: personal_llm_reservations fk_personal_llm_reservations_day; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservations
    ADD CONSTRAINT fk_personal_llm_reservations_day FOREIGN KEY (account_id, utc_day) REFERENCES {{schema}}.personal_llm_budget_days(account_id, utc_day) ON DELETE RESTRICT;


--

-- Name: jobs jobs_agent_task_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.jobs
    ADD CONSTRAINT jobs_agent_task_id_fkey FOREIGN KEY (agent_task_id) REFERENCES {{schema}}.agent_tasks(id) ON DELETE SET NULL;


--

-- Name: jobs jobs_initiated_by_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.jobs
    ADD CONSTRAINT jobs_initiated_by_user_id_fkey FOREIGN KEY (initiated_by_user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: jobs jobs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.jobs
    ADD CONSTRAINT jobs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: llm_attempts llm_attempts_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_attempts
    ADD CONSTRAINT llm_attempts_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: llm_budget_days llm_budget_days_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_budget_days
    ADD CONSTRAINT llm_budget_days_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE RESTRICT;


--

-- Name: llm_budget_reservations llm_budget_reservations_dispatch_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_budget_reservations
    ADD CONSTRAINT llm_budget_reservations_dispatch_id_fkey FOREIGN KEY (dispatch_id) REFERENCES {{schema}}.llm_dispatch_receipts(dispatch_id) ON DELETE RESTRICT;


--

-- Name: llm_budget_reservations llm_budget_reservations_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_budget_reservations
    ADD CONSTRAINT llm_budget_reservations_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE RESTRICT;


--

-- Name: llm_dispatch_receipts llm_dispatch_receipts_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_dispatch_receipts
    ADD CONSTRAINT llm_dispatch_receipts_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE RESTRICT;


--

-- Name: llm_models llm_models_owner_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_models
    ADD CONSTRAINT llm_models_owner_user_id_fkey FOREIGN KEY (owner_user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: llm_outcome_events llm_outcome_events_dispatch_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_outcome_events
    ADD CONSTRAINT llm_outcome_events_dispatch_id_fkey FOREIGN KEY (dispatch_id) REFERENCES {{schema}}.llm_dispatch_receipts(dispatch_id) ON DELETE RESTRICT;


--

-- Name: llm_outcome_events llm_outcome_events_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_outcome_events
    ADD CONSTRAINT llm_outcome_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE RESTRICT;


--

-- Name: llm_providers llm_providers_owner_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.llm_providers
    ADD CONSTRAINT llm_providers_owner_user_id_fkey FOREIGN KEY (owner_user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: notification_preferences notification_preferences_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.notification_preferences
    ADD CONSTRAINT notification_preferences_user_id_fkey FOREIGN KEY (user_id) REFERENCES {{schema}}.users(id) ON DELETE CASCADE;


--

-- Name: notification_receipts notification_receipts_notification_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.notification_receipts
    ADD CONSTRAINT notification_receipts_notification_id_fkey FOREIGN KEY (notification_id) REFERENCES {{schema}}.notifications(id) ON DELETE CASCADE;


--

-- Name: notification_receipts notification_receipts_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.notification_receipts
    ADD CONSTRAINT notification_receipts_user_id_fkey FOREIGN KEY (user_id) REFERENCES {{schema}}.users(id) ON DELETE CASCADE;


--

-- Name: notifications notifications_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.notifications
    ADD CONSTRAINT notifications_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: permissions permissions_model_type_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.permissions
    ADD CONSTRAINT permissions_model_type_id_fkey FOREIGN KEY (model_type_id) REFERENCES {{schema}}.model_types(id);


--

-- Name: personal_billing_accounts personal_billing_accounts_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_billing_accounts
    ADD CONSTRAINT personal_billing_accounts_user_id_fkey FOREIGN KEY (user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: personal_billing_cutovers personal_billing_cutovers_account_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_billing_cutovers
    ADD CONSTRAINT personal_billing_cutovers_account_id_fkey FOREIGN KEY (account_id) REFERENCES {{schema}}.personal_billing_accounts(id) ON DELETE RESTRICT;


--

-- Name: personal_billing_cutovers personal_billing_cutovers_reviewed_by_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_billing_cutovers
    ADD CONSTRAINT personal_billing_cutovers_reviewed_by_user_id_fkey FOREIGN KEY (reviewed_by_user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: personal_legacy_attribution_consents personal_legacy_attribution_consents_account_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attribution_consents
    ADD CONSTRAINT personal_legacy_attribution_consents_account_id_fkey FOREIGN KEY (account_id) REFERENCES {{schema}}.personal_billing_accounts(id) ON DELETE RESTRICT;


--

-- Name: personal_legacy_attribution_consents personal_legacy_attribution_consents_attempt_key_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attribution_consents
    ADD CONSTRAINT personal_legacy_attribution_consents_attempt_key_fkey FOREIGN KEY (attempt_key) REFERENCES {{schema}}.llm_attempts(attempt_key) ON DELETE RESTRICT;


--

-- Name: personal_legacy_attribution_consents personal_legacy_attribution_consents_consented_by_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attribution_consents
    ADD CONSTRAINT personal_legacy_attribution_consents_consented_by_user_id_fkey FOREIGN KEY (consented_by_user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: personal_legacy_attribution_consents personal_legacy_attribution_consents_dispatch_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attribution_consents
    ADD CONSTRAINT personal_legacy_attribution_consents_dispatch_id_fkey FOREIGN KEY (dispatch_id) REFERENCES {{schema}}.llm_dispatch_receipts(dispatch_id) ON DELETE RESTRICT;


--

-- Name: personal_legacy_attribution_consents personal_legacy_attribution_consents_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attribution_consents
    ADD CONSTRAINT personal_legacy_attribution_consents_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE RESTRICT;


--

-- Name: personal_legacy_attributions personal_legacy_attributions_account_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attributions
    ADD CONSTRAINT personal_legacy_attributions_account_id_fkey FOREIGN KEY (account_id) REFERENCES {{schema}}.personal_billing_accounts(id) ON DELETE RESTRICT;


--

-- Name: personal_legacy_attributions personal_legacy_attributions_attempt_key_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attributions
    ADD CONSTRAINT personal_legacy_attributions_attempt_key_fkey FOREIGN KEY (attempt_key) REFERENCES {{schema}}.llm_attempts(attempt_key) ON DELETE RESTRICT;


--

-- Name: personal_legacy_attributions personal_legacy_attributions_consent_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attributions
    ADD CONSTRAINT personal_legacy_attributions_consent_id_fkey FOREIGN KEY (consent_id) REFERENCES {{schema}}.personal_legacy_attribution_consents(id) ON DELETE RESTRICT;


--

-- Name: personal_legacy_attributions personal_legacy_attributions_consented_by_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attributions
    ADD CONSTRAINT personal_legacy_attributions_consented_by_user_id_fkey FOREIGN KEY (consented_by_user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: personal_legacy_attributions personal_legacy_attributions_dispatch_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attributions
    ADD CONSTRAINT personal_legacy_attributions_dispatch_id_fkey FOREIGN KEY (dispatch_id) REFERENCES {{schema}}.llm_dispatch_receipts(dispatch_id) ON DELETE RESTRICT;


--

-- Name: personal_legacy_attributions personal_legacy_attributions_reviewed_by_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attributions
    ADD CONSTRAINT personal_legacy_attributions_reviewed_by_user_id_fkey FOREIGN KEY (reviewed_by_user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: personal_legacy_attributions personal_legacy_attributions_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_legacy_attributions
    ADD CONSTRAINT personal_legacy_attributions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE RESTRICT;


--

-- Name: personal_llm_billing_incidents personal_llm_billing_incidents_account_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_billing_incidents
    ADD CONSTRAINT personal_llm_billing_incidents_account_id_fkey FOREIGN KEY (account_id) REFERENCES {{schema}}.personal_billing_accounts(id) ON DELETE RESTRICT;


--

-- Name: personal_llm_billing_incidents personal_llm_billing_incidents_dispatch_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_billing_incidents
    ADD CONSTRAINT personal_llm_billing_incidents_dispatch_id_fkey FOREIGN KEY (dispatch_id) REFERENCES {{schema}}.personal_llm_reservations(dispatch_id) ON DELETE RESTRICT;


--

-- Name: personal_llm_billing_incidents personal_llm_billing_incidents_event_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_billing_incidents
    ADD CONSTRAINT personal_llm_billing_incidents_event_id_fkey FOREIGN KEY (event_id) REFERENCES {{schema}}.llm_outcome_events(event_id) ON DELETE RESTRICT;


--

-- Name: personal_llm_bound_policies personal_llm_bound_policies_model_row_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_bound_policies
    ADD CONSTRAINT personal_llm_bound_policies_model_row_id_fkey FOREIGN KEY (model_row_id) REFERENCES {{schema}}.llm_models(id) ON DELETE RESTRICT;


--

-- Name: personal_llm_bound_policies personal_llm_bound_policies_provider_row_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_bound_policies
    ADD CONSTRAINT personal_llm_bound_policies_provider_row_id_fkey FOREIGN KEY (provider_row_id) REFERENCES {{schema}}.llm_providers(id) ON DELETE RESTRICT;


--

-- Name: personal_llm_bound_policies personal_llm_bound_policies_reviewed_by_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_bound_policies
    ADD CONSTRAINT personal_llm_bound_policies_reviewed_by_user_id_fkey FOREIGN KEY (reviewed_by_user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: personal_llm_budget_days personal_llm_budget_days_account_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_budget_days
    ADD CONSTRAINT personal_llm_budget_days_account_id_fkey FOREIGN KEY (account_id) REFERENCES {{schema}}.personal_billing_accounts(id) ON DELETE RESTRICT;


--

-- Name: personal_llm_dispatches personal_llm_dispatches_account_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_dispatches
    ADD CONSTRAINT personal_llm_dispatches_account_id_fkey FOREIGN KEY (account_id) REFERENCES {{schema}}.personal_billing_accounts(id) ON DELETE RESTRICT;


--

-- Name: personal_llm_dispatches personal_llm_dispatches_initiated_by_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_dispatches
    ADD CONSTRAINT personal_llm_dispatches_initiated_by_user_id_fkey FOREIGN KEY (initiated_by_user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: personal_llm_dispatches personal_llm_dispatches_payer_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_dispatches
    ADD CONSTRAINT personal_llm_dispatches_payer_user_id_fkey FOREIGN KEY (payer_user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: personal_llm_dispatches personal_llm_dispatches_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_dispatches
    ADD CONSTRAINT personal_llm_dispatches_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE RESTRICT;


--

-- Name: personal_llm_ledger_entries personal_llm_ledger_entries_dispatch_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_ledger_entries
    ADD CONSTRAINT personal_llm_ledger_entries_dispatch_id_fkey FOREIGN KEY (dispatch_id) REFERENCES {{schema}}.personal_llm_reservations(dispatch_id) ON DELETE RESTRICT;


--

-- Name: personal_llm_ledger_entries personal_llm_ledger_entries_event_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_ledger_entries
    ADD CONSTRAINT personal_llm_ledger_entries_event_id_fkey FOREIGN KEY (event_id) REFERENCES {{schema}}.llm_outcome_events(event_id) ON DELETE RESTRICT;


--

-- Name: personal_llm_reservation_states personal_llm_reservation_states_dispatch_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservation_states
    ADD CONSTRAINT personal_llm_reservation_states_dispatch_id_fkey FOREIGN KEY (dispatch_id) REFERENCES {{schema}}.personal_llm_reservations(dispatch_id) ON DELETE RESTRICT;


--

-- Name: personal_llm_reservation_states personal_llm_reservation_states_latest_event_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservation_states
    ADD CONSTRAINT personal_llm_reservation_states_latest_event_id_fkey FOREIGN KEY (latest_event_id) REFERENCES {{schema}}.llm_outcome_events(event_id) ON DELETE RESTRICT;


--

-- Name: personal_llm_reservations personal_llm_reservations_dispatch_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.personal_llm_reservations
    ADD CONSTRAINT personal_llm_reservations_dispatch_id_fkey FOREIGN KEY (dispatch_id) REFERENCES {{schema}}.personal_llm_dispatches(dispatch_id) ON DELETE RESTRICT;


--

-- Name: role_permission role_permission_permission_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.role_permission
    ADD CONSTRAINT role_permission_permission_id_fkey FOREIGN KEY (permission_id) REFERENCES {{schema}}.permissions(id);


--

-- Name: role_permission role_permission_role_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.role_permission
    ADD CONSTRAINT role_permission_role_id_fkey FOREIGN KEY (role_id) REFERENCES {{schema}}.roles(id);


--

-- Name: sources sources_owner_task_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.sources
    ADD CONSTRAINT sources_owner_task_id_fkey FOREIGN KEY (owner_task_id) REFERENCES {{schema}}.agent_tasks(id) ON DELETE CASCADE;


--

-- Name: sources sources_platform_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.sources
    ADD CONSTRAINT sources_platform_id_fkey FOREIGN KEY (platform_id) REFERENCES {{schema}}.platforms(id) ON DELETE CASCADE;


--

-- Name: sources sources_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.sources
    ADD CONSTRAINT sources_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: task_billing_payers task_billing_payers_consented_by_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.task_billing_payers
    ADD CONSTRAINT task_billing_payers_consented_by_user_id_fkey FOREIGN KEY (consented_by_user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: task_billing_payers task_billing_payers_payer_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.task_billing_payers
    ADD CONSTRAINT task_billing_payers_payer_user_id_fkey FOREIGN KEY (payer_user_id) REFERENCES {{schema}}.users(id) ON DELETE RESTRICT;


--

-- Name: task_billing_payers task_billing_payers_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.task_billing_payers
    ADD CONSTRAINT task_billing_payers_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE RESTRICT;


--

-- Name: tenant_channels tenant_channels_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_channels
    ADD CONSTRAINT tenant_channels_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: tenant_invites tenant_invites_role_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_invites
    ADD CONSTRAINT tenant_invites_role_id_fkey FOREIGN KEY (role_id) REFERENCES {{schema}}.workspace_roles(id) ON DELETE SET NULL;


--

-- Name: tenant_invites tenant_invites_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_invites
    ADD CONSTRAINT tenant_invites_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: tenant_users tenant_users_role_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_users
    ADD CONSTRAINT tenant_users_role_id_fkey FOREIGN KEY (role_id) REFERENCES {{schema}}.workspace_roles(id) ON DELETE SET NULL;


--

-- Name: tenant_users tenant_users_tenant_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_users
    ADD CONSTRAINT tenant_users_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE;


--

-- Name: tenant_users tenant_users_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenant_users
    ADD CONSTRAINT tenant_users_user_id_fkey FOREIGN KEY (user_id) REFERENCES {{schema}}.users(id) ON DELETE CASCADE;


--

-- Name: tenants tenants_agent_model_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.tenants
    ADD CONSTRAINT tenants_agent_model_id_fkey FOREIGN KEY (agent_model_id) REFERENCES {{schema}}.llm_models(id) ON DELETE SET NULL;


--

-- Name: user_credentials user_credentials_user_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.user_credentials
    ADD CONSTRAINT user_credentials_user_id_fkey FOREIGN KEY (user_id) REFERENCES {{schema}}.users(id) ON DELETE CASCADE;


--

-- Name: users users_role_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.users
    ADD CONSTRAINT users_role_id_fkey FOREIGN KEY (role_id) REFERENCES {{schema}}.roles(id);


--

-- Name: workspace_role_permission workspace_role_permission_permission_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.workspace_role_permission
    ADD CONSTRAINT workspace_role_permission_permission_id_fkey FOREIGN KEY (permission_id) REFERENCES {{schema}}.permissions(id) ON DELETE CASCADE;


--

-- Name: workspace_role_permission workspace_role_permission_role_id_fkey; Type: FK CONSTRAINT; Schema: {{schema}}; Owner: -
--

ALTER TABLE ONLY {{schema}}.workspace_role_permission
    ADD CONSTRAINT workspace_role_permission_role_id_fkey FOREIGN KEY (role_id) REFERENCES {{schema}}.workspace_roles(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--
