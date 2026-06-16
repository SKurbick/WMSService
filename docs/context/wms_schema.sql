--
-- PostgreSQL database dump
--

-- Dumped from database version 17.4 (Debian 17.4-1.pgdg120+2)
-- Dumped by pg_dump version 17.4 (Debian 17.4-1.pgdg120+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: wms; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA wms;


--
-- Name: SCHEMA wms; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA wms IS 'Warehouse Management System - система управления складом';


--
-- Name: block_empty_container(character varying); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.block_empty_container(p_qr_code character varying) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_container_id BIGINT;
    v_has_contents BOOLEAN;
BEGIN
    -- Проверяем существование контейнера
    SELECT container_id INTO v_container_id
    FROM wms.containers
    WHERE qr_code = p_qr_code;
    
    IF v_container_id IS NULL THEN
        RAISE EXCEPTION 'Container % not found', p_qr_code;
    END IF;
    
    -- Проверяем, есть ли активное содержимое
    SELECT EXISTS(
        SELECT 1 FROM wms.container_contents
        WHERE container_id = v_container_id
          AND status = 'active'
    ) INTO v_has_contents;
    
    IF v_has_contents THEN
        RAISE EXCEPTION 'Container % is not empty, cannot block', p_qr_code;
    END IF;
    
    -- Блокируем контейнер
    UPDATE wms.containers
    SET status = 'blocked',
        updated_at = NOW()
    WHERE container_id = v_container_id;
    
    RETURN TRUE;
END;
$$;


--
-- Name: FUNCTION block_empty_container(p_qr_code character varying); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.block_empty_container(p_qr_code character varying) IS 'Блокирует пустой контейнер, чтобы его нельзя было использовать для хранения товаров. Используется когда контейнер повреждён, загрязнён или выведен из эксплуатации. Пример: SELECT wms.block_empty_container(''QR-00050'');';


--
-- Name: find_available_location(character varying, numeric, character varying); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.find_available_location(p_product_id character varying, p_quantity numeric, p_zone_type character varying DEFAULT 'storage'::character varying) RETURNS TABLE(location_id bigint, location_code character varying, available_space numeric)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT 
        l.location_id,
        l.location_code,
        COALESCE(l.max_weight - SUM(i.quantity * p.weight), l.max_weight) as available_space
    FROM wms.locations l
    LEFT JOIN wms.inventory i ON l.location_id = i.location_id
    LEFT JOIN public.products p ON i.product_id = p.id
    WHERE l.zone_type = p_zone_type
      AND l.is_active = TRUE
      AND l.level = 5  -- Только ячейки
    GROUP BY l.location_id, l.location_code, l.max_weight
    HAVING COALESCE(l.max_weight - SUM(i.quantity * p.weight), l.max_weight) >= 
           (SELECT weight * p_quantity FROM public.products WHERE id = p_product_id)
    ORDER BY available_space DESC
    LIMIT 1;
END;
$$;


--
-- Name: FUNCTION find_available_location(p_product_id character varying, p_quantity numeric, p_zone_type character varying); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.find_available_location(p_product_id character varying, p_quantity numeric, p_zone_type character varying) IS 'Находит оптимальную свободную ячейку для размещения товара с учётом веса и вместимости. Возвращает ячейку с максимальным свободным местом. Пример: SELECT * FROM wms.find_available_location(''wild123'', 50, ''storage'');';


--
-- Name: generate_location_code(); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.generate_location_code() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    parent_code VARCHAR(100);
    base_code VARCHAR(100);
    num_part TEXT;  -- ← ДОБАВЛЕНО: переменная для хранения цифр
BEGIN
    IF NEW.parent_location_id IS NULL THEN
        -- Корневой элемент (склад)
        NEW.location_code := UPPER(REGEXP_REPLACE(NEW.name, '\s+', '-', 'g'));
    ELSE
        -- Получаем код родителя
        SELECT location_code INTO parent_code
        FROM wms.locations
        WHERE location_id = NEW.parent_location_id;

        -- Генерируем код на основе уровня
        CASE NEW.level
            WHEN 1 THEN -- Зона
                NEW.location_code := parent_code || '-' || UPPER(REGEXP_REPLACE(NEW.name, '\s+', '_', 'g'));

            -- ========================================================================
            -- ИСПРАВЛЕНО: LPAD только для однозначных чисел
            -- ========================================================================
            WHEN 2 THEN -- Стеллаж
                num_part := REGEXP_REPLACE(NEW.name, '\D', '', 'g');
                IF LENGTH(num_part) = 1 THEN
                    NEW.location_code := parent_code || '-' || LPAD(num_part, 2, '0');
                ELSE
                    NEW.location_code := parent_code || '-' || num_part;
                END IF;

            WHEN 3 THEN -- Секция
                num_part := REGEXP_REPLACE(NEW.name, '\D', '', 'g');
                IF LENGTH(num_part) = 1 THEN
                    NEW.location_code := parent_code || '-S' || LPAD(num_part, 2, '0');
                ELSE
                    NEW.location_code := parent_code || '-S' || num_part;
                END IF;

            WHEN 4 THEN -- Ярус
                num_part := REGEXP_REPLACE(NEW.name, '\D', '', 'g');
                IF LENGTH(num_part) = 1 THEN
                    NEW.location_code := parent_code || '-L' || LPAD(num_part, 2, '0');
                ELSE
                    NEW.location_code := parent_code || '-L' || num_part;
                END IF;

            WHEN 5 THEN -- Ячейка
                NEW.location_code := parent_code || '-' || UPPER(REGEXP_REPLACE(NEW.name, '\s+', '-', 'g'));

            ELSE
                NEW.location_code := parent_code || '-' || UPPER(REGEXP_REPLACE(NEW.name, '\s+', '-', 'g'));
        END CASE;
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION generate_location_code(); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.generate_location_code() IS 'Автоматически генерирует человекочитаемый код локации на основе иерархии. Пример: PUSHKINO (склад) → PUSHKINO-A (зона) → PUSHKINO-A-01 (стеллаж) → PUSHKINO-A-01-S05 (секция) → PUSHKINO-A-01-S05-L02 (ярус) → PUSHKINO-A-01-S05-L02-B (ячейка)';


--
-- Name: generate_location_path(); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.generate_location_path() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    parent_path wms.ltree;
BEGIN
    IF NEW.parent_location_id IS NULL THEN
        NEW.path := NEW.location_id::TEXT::wms.ltree;
    ELSE
        SELECT path INTO parent_path
        FROM wms.locations
        WHERE location_id = NEW.parent_location_id;

        IF parent_path IS NULL THEN
            RAISE EXCEPTION 'Parent location % not found', NEW.parent_location_id;
        END IF;

        -- явно добавляем точку
        NEW.path := (parent_path::TEXT || '.' || NEW.location_id::TEXT)::wms.ltree;
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION generate_location_path(); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.generate_location_path() IS 'Автоматически генерирует LTREE path при создании/обновлении локации. Пример: склад(1) > зона(2) > стеллаж(4) → path = 1.2.4. Триггер срабатывает при INSERT и UPDATE parent_location_id.';


--
-- Name: get_approvers(); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.get_approvers() RETURNS TABLE(user_id integer, username character varying, full_name character varying, email character varying)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT 
        u.id,
        u.username,
        u.full_name,
        u.email
    FROM public.users u
    JOIN public.user_permissions up ON u.id = up.user_id
    WHERE up.approve_discrepancies = TRUE
      AND u.disabled = FALSE
    ORDER BY u.full_name;
END;
$$;


--
-- Name: FUNCTION get_approvers(); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.get_approvers() IS 'Получить список пользователей с правом подтверждать расхождения';


--
-- Name: get_child_locations(bigint); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.get_child_locations(p_location_id bigint) RETURNS TABLE(location_id bigint, location_code character varying, level integer, path wms.ltree)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_path wms.ltree;
BEGIN
    -- Получаем путь родительской локации
    SELECT l.path INTO v_path
    FROM wms.locations l
    WHERE l.location_id = p_location_id;
    
    IF v_path IS NULL THEN
        RAISE EXCEPTION 'Location % not found', p_location_id;
    END IF;
    
    -- Возвращаем все дочерние локации через LTREE
    RETURN QUERY
    SELECT l.location_id, l.location_code, l.level, l.path
    FROM wms.locations l
    WHERE l.path <@ v_path  -- Оператор LTREE: является потомком
      AND l.location_id != p_location_id  -- Исключаем саму локацию
    ORDER BY l.path;
END;
$$;


--
-- Name: FUNCTION get_child_locations(p_location_id bigint); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.get_child_locations(p_location_id bigint) IS 'Возвращает все дочерние локации используя LTREE оператор <@. Пример: для стеллажа вернёт все секции, ярусы и ячейки. Вызов: SELECT * FROM wms.get_child_locations(4);';


--
-- Name: get_task_items_summary(bigint); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.get_task_items_summary(p_task_id bigint) RETURNS TABLE(item_id bigint, product_id character varying, product_name character varying, quantity_planned numeric, quantity_actual numeric, from_location_code character varying, discrepancy_reason text)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT 
        ti.item_id,  -- добавляем
        ti.product_id,
        p.name as product_name,
        ti.quantity_planned,
        ti.quantity_actual,
        l.location_code as from_location_code,
        ti.discrepancy_reason
    FROM wms.task_items ti
    LEFT JOIN public.products p ON ti.product_id = p.id
    LEFT JOIN wms.locations l ON ti.from_location_id = l.location_id
    WHERE ti.task_id = p_task_id
    ORDER BY ti.item_id;
END;
$$;


--
-- Name: FUNCTION get_task_items_summary(p_task_id bigint); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.get_task_items_summary(p_task_id bigint) IS 'Получить список товаров в заявке с деталями';


--
-- Name: move_container_inventory(); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.move_container_inventory() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.location_id IS DISTINCT FROM NEW.location_id THEN
        
        -- 1. Логируем перемещение в movements (с batch_number!)
        INSERT INTO wms.movements (
            movement_type, product_id, 
            from_location_id, to_location_id, 
            quantity, batch_number, container_code, reason
        )
        SELECT 
            'transfer', 
            i.product_id,
            OLD.location_id,
            NEW.location_id,
            i.quantity,
            i.batch_number,  -- ← КРИТИЧНО для избежания дублирования!
            NEW.qr_code,
            'Container moved'
        FROM wms.inventory i
        WHERE i.container_code = NEW.qr_code;
        
        -- 2. НЕ обновляем inventory напрямую!
        --    Триггер update_inventory_from_movement() сделает это автоматически
        
    END IF;
    
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION move_container_inventory(); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.move_container_inventory() IS 'При перемещении контейнера автоматически перемещает всё его содержимое в INVENTORY и логирует каждое движение товара в MOVEMENTS. Триггер срабатывает только при изменении location_id.';


--
-- Name: register_container(character varying, character varying, character varying, jsonb); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.register_container(p_qr_code character varying, p_container_type character varying, p_location_code character varying, p_contents jsonb) RETURNS TABLE(container_id bigint, qr_code character varying, items_registered integer)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_container_id BIGINT;
    v_location_id BIGINT;
    v_item JSONB;
    v_count INTEGER := 0;
    v_existing_container BOOLEAN;
BEGIN
    -- ============================================================================
    -- ЗАЩИТА: Проверка существования контейнера с таким QR
    -- ============================================================================
		SELECT EXISTS(
		    SELECT 1 FROM wms.containers c WHERE c.qr_code = p_qr_code
		) INTO v_existing_container;
    
    IF v_existing_container THEN
        RAISE EXCEPTION 'Container with QR code % already exists. Cannot register twice.', p_qr_code;
    END IF;
    
    -- ============================================================================
    -- Основная логика (без изменений)
    -- ============================================================================
    
    -- 1. Получаем location_id
    SELECT location_id INTO v_location_id
    FROM wms.locations
    WHERE location_code = p_location_code;
    
    IF v_location_id IS NULL THEN
        RAISE EXCEPTION 'Location % not found', p_location_code;
    END IF;
    
    -- 2. Создаём контейнер
    INSERT INTO wms.containers (qr_code, container_type, location_id, status)
    VALUES (p_qr_code, p_container_type, v_location_id, 'sealed')
    RETURNING wms.containers.container_id INTO v_container_id;
    
    -- 3. Добавляем содержимое
    FOR v_item IN SELECT * FROM jsonb_array_elements(p_contents)
    LOOP
        INSERT INTO wms.container_contents (
            container_id, product_id, quantity, 
            batch_number, is_scanned
        )
        VALUES (
            v_container_id,
            v_item->>'product_id',
            (v_item->>'quantity')::DECIMAL,
            v_item->>'batch_number',
            COALESCE((v_item->>'is_scanned')::BOOLEAN, FALSE)
        );
        
        -- Триггер sync_container_to_inventory() автоматически создаст movements
        v_count := v_count + 1;
    END LOOP;
    
    -- 4. Возвращаем результат
    RETURN QUERY
    SELECT v_container_id, p_qr_code, v_count;
END;
$$;


--
-- Name: FUNCTION register_container(p_qr_code character varying, p_container_type character varying, p_location_code character varying, p_contents jsonb); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.register_container(p_qr_code character varying, p_container_type character varying, p_location_code character varying, p_contents jsonb) IS 'Регистрирует новый контейнер. ЗАЩИТА: Проверяет, что QR-код ещё не зарегистрирован (предотвращает двойную регистрацию). Пример: SELECT * FROM wms.register_container(''QR-00001'', ''pallet'', ''PUSHKINO-ПРИЁМКА'', ''[...]''::jsonb);';


--
-- Name: sync_container_to_inventory(); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.sync_container_to_inventory() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_location_id BIGINT;
    v_qr_code VARCHAR(100);
BEGIN
    -- Работаем только с активными записями
    IF NEW.status != 'active' THEN
        RETURN NEW;
    END IF;
    
    -- Получаем location_id и qr_code контейнера
    SELECT location_id, qr_code INTO v_location_id, v_qr_code
    FROM wms.containers
    WHERE container_id = NEW.container_id;
    
    IF v_location_id IS NULL THEN
        RAISE EXCEPTION 'Container % has no location assigned', NEW.container_id;
    END IF;
    
    -- ВМЕСТО прямого INSERT в inventory → создаём movements!
    -- Триггер update_inventory_from_movement() сам создаст inventory
    INSERT INTO wms.movements (
        movement_type,
        product_id,
        from_location_id,
        to_location_id,
        quantity,
        batch_number,
        container_code,
        reason
    )
    VALUES (
        'receive',              -- ← Тип: приёмка
        NEW.product_id,
        NULL,                   -- ← from = NULL (пришло извне)
        v_location_id,          -- ← to = зона приёмки
        NEW.quantity,
        NEW.batch_number,
        v_qr_code,
        'Container registered'
    );
    
    -- Триггер update_inventory_from_movement() автоматически создаст inventory!
    
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION sync_container_to_inventory(); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.sync_container_to_inventory() IS 'Автоматически добавляет товар в INVENTORY при регистрации содержимого контейнера. Срабатывает только для записей со status=active. При конфликте (товар уже есть в INVENTORY) увеличивает quantity.';


--
-- Name: unpack_from_container(character varying, character varying, numeric); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.unpack_from_container(p_qr_code character varying, p_product_id character varying, p_quantity numeric) RETURNS TABLE(success boolean, remaining_in_container numeric, loose_quantity numeric)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_container_id BIGINT;
    v_location_id BIGINT;
    v_current_qty DECIMAL;
    v_batch_number VARCHAR(50);
BEGIN
    -- 1. Получаем контейнер
    SELECT container_id, location_id INTO v_container_id, v_location_id
    FROM wms.containers
    WHERE qr_code = p_qr_code;
    
    IF v_container_id IS NULL THEN
        RAISE EXCEPTION 'Container % not found', p_qr_code;
    END IF;
    
    -- 2. Проверяем количество в контейнере
    SELECT quantity, batch_number INTO v_current_qty, v_batch_number
    FROM wms.container_contents
    WHERE container_id = v_container_id 
      AND product_id = p_product_id
      AND status = 'active';
    
    IF v_current_qty IS NULL OR v_current_qty < p_quantity THEN
        RAISE EXCEPTION 'Not enough quantity in container. Available: %, requested: %', 
                        COALESCE(v_current_qty, 0), p_quantity;
    END IF;
    
    -- ============================================================================
    -- ИЗМЕНЕНИЕ: Не создаём новую запись, а ОБНОВЛЯЕМ существующую!
    -- ============================================================================
    
    -- 3. Обновляем количество в container_contents (НЕ INSERT!)
    UPDATE wms.container_contents
    SET quantity = v_current_qty - p_quantity,
        updated_at = NOW()
    WHERE container_id = v_container_id 
      AND product_id = p_product_id
      AND status = 'active';
    
    -- 4. Если количество стало 0 → меняем status на 'empty'
    UPDATE wms.container_contents
    SET status = 'empty'
    WHERE container_id = v_container_id 
      AND product_id = p_product_id
      AND quantity = 0
      AND status = 'active';
    
    -- ============================================================================
    -- Movements (без изменений)
    -- ============================================================================
    
    -- 5. Логируем убыль из контейнера
    INSERT INTO wms.movements (
        movement_type, product_id, 
        from_location_id, to_location_id,
        quantity, batch_number, container_code, reason
    )
    VALUES (
        'unpack', 
        p_product_id, 
        v_location_id,
        NULL,
        p_quantity,
        v_batch_number,
        p_qr_code, 
        'Unpacked from container'
    );
    
    -- 6. Логируем прибыль в россыпь
    INSERT INTO wms.movements (
        movement_type, product_id, 
        from_location_id, to_location_id,
        quantity, batch_number, container_code, reason
    )
    VALUES (
        'unpack', 
        p_product_id, 
        NULL,
        v_location_id,
        p_quantity,
        v_batch_number,
        NULL,
        'Unpacked from ' || p_qr_code
    );
    
    -- 7. Меняем статус контейнера на opened
    UPDATE wms.containers 
    SET status = 'opened' 
    WHERE container_id = v_container_id AND status = 'sealed';
    
    -- 8. Возвращаем результат
    RETURN QUERY
    SELECT 
        TRUE,
        COALESCE(v_current_qty - p_quantity, 0),
        p_quantity;
END;
$$;


--
-- Name: FUNCTION unpack_from_container(p_qr_code character varying, p_product_id character varying, p_quantity numeric); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.unpack_from_container(p_qr_code character varying, p_product_id character varying, p_quantity numeric) IS 'Вскрывает контейнер и извлекает указанное количество товара в россыпь. Использует soft delete (status=replaced) для сохранения истории. Автоматически обновляет INVENTORY через триггеры в MOVEMENTS. Пример: SELECT * FROM wms.unpack_from_container(''QR-00001'', ''wild123'', 10);';


--
-- Name: update_containers_timestamp(); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.update_containers_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_fbs_item_updated_at(); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.update_fbs_item_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


--
-- Name: update_inventory_from_movement(); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.update_inventory_from_movement() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    affected_location_id BIGINT;
    change_quantity DECIMAL(10,2);
    current_qty DECIMAL(10,2);
BEGIN
    -- Если товар ПРИШЁЛ в локацию (to_location_id)
    IF NEW.to_location_id IS NOT NULL THEN
        affected_location_id := NEW.to_location_id;
        change_quantity := NEW.quantity;

        INSERT INTO wms.inventory (product_id, location_id, quantity, status, batch_number, container_code)
        VALUES (NEW.product_id, affected_location_id, change_quantity, 'available', NEW.batch_number, NEW.container_code)
        ON CONFLICT (product_id, location_id, status, batch_number, container_code)
        DO UPDATE SET
            quantity = wms.inventory.quantity + EXCLUDED.quantity,
            updated_at = NOW();
    END IF;

    -- Если товар УШЁЛ из локации (from_location_id)
    IF NEW.from_location_id IS NOT NULL THEN
        affected_location_id := NEW.from_location_id;
        change_quantity := -ABS(NEW.quantity);

        UPDATE wms.inventory
        SET quantity = quantity + change_quantity,
            updated_at = NOW()
        WHERE product_id = NEW.product_id
          AND location_id = affected_location_id
          AND status = 'available'
          AND COALESCE(batch_number, '') = COALESCE(NEW.batch_number, '')
          AND COALESCE(container_code, '') = COALESCE(NEW.container_code, '');

        IF NOT FOUND AND NEW.movement_type IN ('ship', 'transfer') THEN
            -- Узнаём сколько реально есть на локации
            SELECT COALESCE(SUM(quantity), 0) INTO current_qty
            FROM wms.inventory
            WHERE product_id = NEW.product_id
              AND location_id = affected_location_id;

            RAISE EXCEPTION 'Недостаточно остатка для списания: product_id=%, location_id=%, movement_type=%, запрошено=%, текущий остаток=%',
                NEW.product_id, affected_location_id, NEW.movement_type, ABS(change_quantity), current_qty;
        END IF;

        DELETE FROM wms.inventory
        WHERE product_id = NEW.product_id
          AND location_id = affected_location_id
          AND quantity <= 0;
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION update_inventory_from_movement(); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.update_inventory_from_movement() IS 'Автоматически обновляет INVENTORY при добавлении события в MOVEMENTS. Реализует Event Sourcing → Materialized State. При to_location_id добавляет товар, при from_location_id убирает. Записи с quantity <= 0 удаляются автоматически.';


--
-- Name: update_inventory_timestamp(); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.update_inventory_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION update_inventory_timestamp(); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.update_inventory_timestamp() IS 'Автоматически обновляет поле updated_at при любом изменении записи в INVENTORY.';


--
-- Name: update_locations_timestamp(); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.update_locations_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_updated_at_column(); Type: FUNCTION; Schema: wms; Owner: -
--

CREATE FUNCTION wms.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION update_updated_at_column(); Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON FUNCTION wms.update_updated_at_column() IS 'Автоматически обновляет поле updated_at при UPDATE записи';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: container_contents; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.container_contents (
    content_id bigint NOT NULL,
    container_id bigint NOT NULL,
    product_id character varying(50) NOT NULL,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    is_scanned boolean DEFAULT false,
    status character varying(20) DEFAULT 'active'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT chk_content_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'replaced'::character varying, 'removed'::character varying])::text[]))),
    CONSTRAINT container_contents_quantity_check CHECK ((quantity > (0)::numeric))
);


--
-- Name: TABLE container_contents; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON TABLE wms.container_contents IS 'Содержимое контейнеров. Один контейнер может содержать несколько разных товаров (смешанное содержимое). Поддерживает soft delete через поле status для сохранения истории.';


--
-- Name: COLUMN container_contents.batch_number; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.container_contents.batch_number IS 'Номер партии товара. Используется для трассировки (откуда пришло, куда ушло) и отзыва брака. Пример: BATCH-2026-001 = партия от 15 января 2026';


--
-- Name: COLUMN container_contents.is_scanned; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.container_contents.is_scanned IS 'Был ли товар физически отсканирован? FALSE = данные взяты из накладной без физической проверки, TRUE = товар отсканирован штрих-кодом (проверено наличие)';


--
-- Name: COLUMN container_contents.status; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.container_contents.status IS 'active = актуальная запись (используется), replaced = заменена при разукомплектации (история), removed = удалена (soft delete, история сохранена)';


--
-- Name: container_contents_content_id_seq; Type: SEQUENCE; Schema: wms; Owner: -
--

CREATE SEQUENCE wms.container_contents_content_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: container_contents_content_id_seq; Type: SEQUENCE OWNED BY; Schema: wms; Owner: -
--

ALTER SEQUENCE wms.container_contents_content_id_seq OWNED BY wms.container_contents.content_id;


--
-- Name: containers; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.containers (
    container_id bigint NOT NULL,
    qr_code character varying(100) NOT NULL,
    container_type character varying(50) NOT NULL,
    parent_container_id bigint,
    location_id bigint,
    status character varying(50) DEFAULT 'sealed'::character varying,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT chk_container_status CHECK (((status)::text = ANY ((ARRAY['sealed'::character varying, 'opened'::character varying, 'empty'::character varying, 'blocked'::character varying])::text[]))),
    CONSTRAINT chk_container_type CHECK (((container_type)::text = ANY ((ARRAY['pallet'::character varying, 'box'::character varying, 'unit'::character varying])::text[])))
);


--
-- Name: TABLE containers; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON TABLE wms.containers IS 'Универсальные контейнеры с QR-кодами. QR-код печатается заранее (на бабинах), тип определяется при регистрации. Поддерживает вложенность (коробы в палетах, единицы в коробах).';


--
-- Name: COLUMN containers.qr_code; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.containers.qr_code IS 'Универсальный QR-код. Печатается заранее на бабинах, не содержит информации о типе. Тип определяется при регистрации в системе. Пример: QR-000001, QR-012345';


--
-- Name: COLUMN containers.container_type; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.containers.container_type IS 'Тип контейнера. Указывается при регистрации в системе (не в самом QR-коде). pallet = палета, box = короб, unit = единица товара с индивидуальным QR';


--
-- Name: COLUMN containers.parent_container_id; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.containers.parent_container_id IS 'Вложенность контейнеров. Пример: короб (QR-00002) внутри палеты (QR-00001). NULL = самостоятельный контейнер без родителя';


--
-- Name: COLUMN containers.status; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.containers.status IS 'sealed = запечатан (не вскрывали), opened = вскрыт (извлекали товар), empty = пустой (можно переиспользовать QR), blocked = заблокирован администратором (нельзя использовать)';


--
-- Name: containers_container_id_seq; Type: SEQUENCE; Schema: wms; Owner: -
--

CREATE SEQUENCE wms.containers_container_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: containers_container_id_seq; Type: SEQUENCE OWNED BY; Schema: wms; Owner: -
--

ALTER SEQUENCE wms.containers_container_id_seq OWNED BY wms.containers.container_id;


--
-- Name: fbs_shipment_items; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.fbs_shipment_items (
    item_id integer NOT NULL,
    shipment_id integer NOT NULL,
    product_id character varying NOT NULL,
    quantity integer NOT NULL,
    author character varying NOT NULL,
    supply_id character varying NOT NULL,
    account character varying NOT NULL,
    assembly_tasks jsonb NOT NULL,
    warehouse_id integer NOT NULL,
    delivery_type character varying NOT NULL,
    wb_warehouse character varying,
    shipment_date timestamp with time zone,
    status character varying(20) DEFAULT 'new'::character varying NOT NULL,
    error_message text,
    retry_count integer DEFAULT 0 NOT NULL,
    max_retries integer DEFAULT 5 NOT NULL,
    next_retry_at timestamp with time zone,
    movement_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_fbs_item_status CHECK (((status)::text = ANY ((ARRAY['new'::character varying, 'success'::character varying, 'failed'::character varying, 'pending_retry'::character varying, 'retry_exhausted'::character varying])::text[])))
);


--
-- Name: fbs_shipment_items_item_id_seq; Type: SEQUENCE; Schema: wms; Owner: -
--

CREATE SEQUENCE wms.fbs_shipment_items_item_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fbs_shipment_items_item_id_seq; Type: SEQUENCE OWNED BY; Schema: wms; Owner: -
--

ALTER SEQUENCE wms.fbs_shipment_items_item_id_seq OWNED BY wms.fbs_shipment_items.item_id;


--
-- Name: fbs_shipments; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.fbs_shipments (
    shipment_id integer NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    raw_message jsonb NOT NULL,
    total_items integer NOT NULL,
    status character varying(30) DEFAULT 'processing'::character varying NOT NULL,
    source character varying(30) DEFAULT 'standard'::character varying NOT NULL,
    error_message text,
    completed_at timestamp with time zone,
    CONSTRAINT chk_fbs_shipments_source CHECK (((source)::text = ANY ((ARRAY['standard'::character varying, 'external_detected'::character varying])::text[]))),
    CONSTRAINT chk_fbs_shipments_status CHECK (((status)::text = ANY ((ARRAY['processing'::character varying, 'completed'::character varying, 'partially_completed'::character varying, 'failed'::character varying, 'validation_failed'::character varying])::text[])))
);


--
-- Name: fbs_shipments_shipment_id_seq; Type: SEQUENCE; Schema: wms; Owner: -
--

CREATE SEQUENCE wms.fbs_shipments_shipment_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fbs_shipments_shipment_id_seq; Type: SEQUENCE OWNED BY; Schema: wms; Owner: -
--

ALTER SEQUENCE wms.fbs_shipments_shipment_id_seq OWNED BY wms.fbs_shipments.shipment_id;


--
-- Name: inventory; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.inventory (
    inventory_id bigint NOT NULL,
    product_id character varying(50) NOT NULL,
    location_id bigint NOT NULL,
    quantity numeric(10,2) NOT NULL,
    status character varying(50) DEFAULT 'available'::character varying,
    batch_number character varying(50),
    container_code character varying(100),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT chk_inventory_status CHECK (((status)::text = ANY ((ARRAY['available'::character varying, 'damaged'::character varying, 'quarantine'::character varying])::text[]))),
    CONSTRAINT inventory_quantity_check CHECK ((quantity >= (0)::numeric))
);


--
-- Name: TABLE inventory; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON TABLE wms.inventory IS 'Материализованные остатки товаров. Текущее состояние склада для быстрых запросов. Обновляется автоматически триггерами из MOVEMENTS (Event Sourcing). Можно удалять записи с quantity=0, т.к. это "кэш" — источник правды в MOVEMENTS.';


--
-- Name: COLUMN inventory.quantity; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.inventory.quantity IS 'Текущее количество товара в этой локации. При достижении 0 запись автоматически удаляется триггером. Можно пересчитать из MOVEMENTS.';


--
-- Name: COLUMN inventory.status; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.inventory.status IS 'available = доступен для комплектации и отгрузки, damaged = повреждённый товар (нельзя отгружать), quarantine = на карантине (проверка качества)';


--
-- Name: COLUMN inventory.container_code; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.inventory.container_code IS 'QR-код контейнера, в котором лежит товар. NULL = товар россыпью (не в контейнере, лежит отдельно). Пример: QR-000001 (в палете), QR-00050 (в коробе), NULL (россыпь)';


--
-- Name: inventory_inventory_id_seq; Type: SEQUENCE; Schema: wms; Owner: -
--

CREATE SEQUENCE wms.inventory_inventory_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: inventory_inventory_id_seq; Type: SEQUENCE OWNED BY; Schema: wms; Owner: -
--

ALTER SEQUENCE wms.inventory_inventory_id_seq OWNED BY wms.inventory.inventory_id;


--
-- Name: inventory_snapshots; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.inventory_snapshots (
    snapshot_id bigint NOT NULL,
    snapshot_date date NOT NULL,
    product_id character varying(50) NOT NULL,
    location_id bigint NOT NULL,
    container_code character varying(100),
    quantity numeric(10,2) NOT NULL,
    status character varying(50),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE inventory_snapshots; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON TABLE wms.inventory_snapshots IS 'Снимки остатков для быстрого доступа к истории. Создаются раз в сутки (например, в 00:00). Позволяют быстро узнать остатки на прошлую дату без сканирования всей таблицы MOVEMENTS. Пример: "сколько было wild123 на 15 января 2026?"';


--
-- Name: COLUMN inventory_snapshots.snapshot_date; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.inventory_snapshots.snapshot_date IS 'Дата, на которую сделан снимок остатков. Позволяет быстро ответить на вопрос "сколько было товара на конкретную дату?" без пересчёта из MOVEMENTS.';


--
-- Name: inventory_snapshots_snapshot_id_seq; Type: SEQUENCE; Schema: wms; Owner: -
--

CREATE SEQUENCE wms.inventory_snapshots_snapshot_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: inventory_snapshots_snapshot_id_seq; Type: SEQUENCE OWNED BY; Schema: wms; Owner: -
--

ALTER SEQUENCE wms.inventory_snapshots_snapshot_id_seq OWNED BY wms.inventory_snapshots.snapshot_id;


--
-- Name: locations; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.locations (
    location_id bigint NOT NULL,
    parent_location_id bigint,
    location_code character varying(100) NOT NULL,
    path wms.ltree NOT NULL,
    name character varying(255) NOT NULL,
    zone_type character varying(50),
    level integer NOT NULL,
    max_weight numeric(10,2),
    max_volume numeric(10,3),
    is_active boolean DEFAULT true,
    is_pickable boolean DEFAULT false,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT chk_zone_type CHECK (((zone_type)::text = ANY ((ARRAY['receiving'::character varying, 'storage'::character varying, 'picking'::character varying, 'packing'::character varying, 'shipping'::character varying, 'quarantine'::character varying, NULL::character varying])::text[])))
);


--
-- Name: TABLE locations; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON TABLE wms.locations IS 'Иерархическая структура склада через LTREE. Хранит все локации от склада до ячеек. Пример иерархии: Склад PUSHKINO > Зона A > Стеллаж 01 > Секция 05 > Ярус 02 > Ячейка B';


--
-- Name: COLUMN locations.location_code; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.locations.location_code IS 'Человекочитаемый код. Автогенерируется триггером из названий родителей. Пример: PUSHKINO-A-01-S05-L02-B = Склад Pushkino, Зона A, Стеллаж 01, Секция 05, Ярус 02, Ячейка B';


--
-- Name: COLUMN locations.path; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.locations.path IS 'LTREE путь для быстрых иерархических запросов. Пример: 1.2.4 означает Склад(1) > Зона(2) > Стеллаж(4). Используется для запросов типа "найти все дочерние локации"';


--
-- Name: COLUMN locations.zone_type; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.locations.zone_type IS 'Тип зоны склада. receiving = приёмка, storage = хранение, picking = комплектация, packing = упаковка, shipping = отгрузка, quarantine = карантин';


--
-- Name: COLUMN locations.max_weight; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.locations.max_weight IS 'Максимальный вес товаров в килограммах, который может выдержать локация. Используется для проверки при размещении.';


--
-- Name: COLUMN locations.max_volume; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.locations.max_volume IS 'Максимальный объём товаров в кубических метрах. Используется вместе с products.volume для расчёта вместимости.';


--
-- Name: COLUMN locations.is_pickable; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.locations.is_pickable IS 'Можно ли комплектовать товар из этой локации (обычно TRUE для picking zone, FALSE для storage)';


--
-- Name: locations_location_id_seq; Type: SEQUENCE; Schema: wms; Owner: -
--

CREATE SEQUENCE wms.locations_location_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: locations_location_id_seq; Type: SEQUENCE OWNED BY; Schema: wms; Owner: -
--

ALTER SEQUENCE wms.locations_location_id_seq OWNED BY wms.locations.location_id;


--
-- Name: movements; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements (
    movement_id bigint NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
)
PARTITION BY RANGE (created_at);


--
-- Name: TABLE movements; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON TABLE wms.movements IS 'Event Sourcing — все события движения товаров. Append-only таблица (только INSERT, никогда UPDATE/DELETE). Источник правды для пересчёта INVENTORY. Партиционирована по месяцам для производительности. Хранит полную историю всех операций с товарами.';


--
-- Name: COLUMN movements.movement_type; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.movements.movement_type IS 'receive = приёмка товара на склад, putaway = размещение в ячейку хранения, transfer = перемещение между ячейками, pick = комплектация для отгрузки, ship = отгрузка со склада, unpack = вскрытие контейнера (разукомплектация), adjust = корректировка остатков';


--
-- Name: COLUMN movements.from_location_id; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.movements.from_location_id IS 'Откуда перемещён товар. NULL = товар поступил извне (receive - приёмка от поставщика)';


--
-- Name: COLUMN movements.to_location_id; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.movements.to_location_id IS 'Куда перемещён товар. NULL = товар покинул склад (ship - отгрузка клиенту)';


--
-- Name: COLUMN movements.quantity; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.movements.quantity IS 'Количество товара. Может быть отрицательным (например, при unpack: -50 убрали из контейнера, +50 добавили россыпью). Обычно положительное для receive, transfer, pick.';


--
-- Name: COLUMN movements.reason; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.movements.reason IS 'Причина движения товара. Свободный текст для аудита. Пример: "Packed to QR-00001" (упаковали в палету), "Damaged during handling" (повреждён при погрузке)';


--
-- Name: movements_movement_id_seq; Type: SEQUENCE; Schema: wms; Owner: -
--

CREATE SEQUENCE wms.movements_movement_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: movements_movement_id_seq; Type: SEQUENCE OWNED BY; Schema: wms; Owner: -
--

ALTER SEQUENCE wms.movements_movement_id_seq OWNED BY wms.movements.movement_id;


--
-- Name: movements_2026_01; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements_2026_01 (
    movement_id bigint DEFAULT nextval('wms.movements_movement_id_seq'::regclass) NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
);


--
-- Name: movements_2026_02; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements_2026_02 (
    movement_id bigint DEFAULT nextval('wms.movements_movement_id_seq'::regclass) NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
);


--
-- Name: movements_2026_03; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements_2026_03 (
    movement_id bigint DEFAULT nextval('wms.movements_movement_id_seq'::regclass) NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
);


--
-- Name: movements_2026_04; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements_2026_04 (
    movement_id bigint DEFAULT nextval('wms.movements_movement_id_seq'::regclass) NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
);


--
-- Name: movements_2026_05; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements_2026_05 (
    movement_id bigint DEFAULT nextval('wms.movements_movement_id_seq'::regclass) NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
);


--
-- Name: movements_2026_06; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements_2026_06 (
    movement_id bigint DEFAULT nextval('wms.movements_movement_id_seq'::regclass) NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
);


--
-- Name: movements_2026_07; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements_2026_07 (
    movement_id bigint DEFAULT nextval('wms.movements_movement_id_seq'::regclass) NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
);


--
-- Name: movements_2026_08; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements_2026_08 (
    movement_id bigint DEFAULT nextval('wms.movements_movement_id_seq'::regclass) NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
);


--
-- Name: movements_2026_09; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements_2026_09 (
    movement_id bigint DEFAULT nextval('wms.movements_movement_id_seq'::regclass) NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
);


--
-- Name: movements_2026_10; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements_2026_10 (
    movement_id bigint DEFAULT nextval('wms.movements_movement_id_seq'::regclass) NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
);


--
-- Name: movements_2026_11; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements_2026_11 (
    movement_id bigint DEFAULT nextval('wms.movements_movement_id_seq'::regclass) NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
);


--
-- Name: movements_2026_12; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.movements_2026_12 (
    movement_id bigint DEFAULT nextval('wms.movements_movement_id_seq'::regclass) NOT NULL,
    movement_type character varying(50) NOT NULL,
    product_id character varying(50) NOT NULL,
    from_location_id bigint,
    to_location_id bigint,
    quantity numeric(10,2) NOT NULL,
    batch_number character varying(50),
    container_code character varying(100),
    from_container_id bigint,
    to_container_id bigint,
    user_name character varying(100),
    reason text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying])::text[])))
);


--
-- Name: mv_product_stock; Type: MATERIALIZED VIEW; Schema: wms; Owner: -
--

CREATE MATERIALIZED VIEW wms.mv_product_stock AS
 SELECT product_id,
    sum(quantity) AS total_quantity,
    count(DISTINCT location_id) AS locations_count,
    sum(
        CASE
            WHEN (container_code IS NOT NULL) THEN quantity
            ELSE (0)::numeric
        END) AS in_containers,
    sum(
        CASE
            WHEN (container_code IS NULL) THEN quantity
            ELSE (0)::numeric
        END) AS loose
   FROM wms.inventory
  WHERE ((status)::text = 'available'::text)
  GROUP BY product_id
  WITH NO DATA;


--
-- Name: MATERIALIZED VIEW mv_product_stock; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON MATERIALIZED VIEW wms.mv_product_stock IS 'Материализованное представление для быстрого доступа к агрегированным остаткам. Обновляется периодически командой: REFRESH MATERIALIZED VIEW CONCURRENTLY wms.mv_product_stock; Рекомендуется обновлять раз в час или по расписанию.';


--
-- Name: notifications; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.notifications (
    notification_id bigint NOT NULL,
    user_id integer NOT NULL,
    notification_type character varying(50) NOT NULL,
    title text NOT NULL,
    message text NOT NULL,
    severity character varying(20) DEFAULT 'info'::character varying,
    related_task_id bigint,
    metadata jsonb,
    is_read boolean DEFAULT false,
    read_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE notifications; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON TABLE wms.notifications IS 'Уведомления для менеджеров о расхождениях и событиях';


--
-- Name: COLUMN notifications.severity; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.notifications.severity IS 'info, warning, critical';


--
-- Name: COLUMN notifications.metadata; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.notifications.metadata IS 'Дополнительные данные в формате JSON';


--
-- Name: notifications_notification_id_seq; Type: SEQUENCE; Schema: wms; Owner: -
--

CREATE SEQUENCE wms.notifications_notification_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notifications_notification_id_seq; Type: SEQUENCE OWNED BY; Schema: wms; Owner: -
--

ALTER SEQUENCE wms.notifications_notification_id_seq OWNED BY wms.notifications.notification_id;


--
-- Name: receipt_items; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.receipt_items (
    receipt_item_id bigint NOT NULL,
    guid character varying(255) NOT NULL,
    product_id character varying(255) NOT NULL,
    quantity numeric(15,2) NOT NULL,
    document_number character varying(255),
    supplier_name character varying(500),
    supplier_code character varying(255),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT chk_receipt_quantity CHECK ((quantity >= (0)::numeric))
);


--
-- Name: TABLE receipt_items; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON TABLE wms.receipt_items IS 'Поступления товаров из 1С - snapshot для отслеживания изменений и корректировки';


--
-- Name: COLUMN receipt_items.guid; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.receipt_items.guid IS 'GUID документа поставки из 1С (уникален с product_id)';


--
-- Name: COLUMN receipt_items.quantity; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.receipt_items.quantity IS 'Количество товара в поставке на момент последнего обновления';


--
-- Name: COLUMN receipt_items.document_number; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.receipt_items.document_number IS 'Номер документа поставки из 1С';


--
-- Name: COLUMN receipt_items.supplier_code; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.receipt_items.supplier_code IS 'Код поставщика (ИНН)';


--
-- Name: receipt_items_receipt_item_id_seq; Type: SEQUENCE; Schema: wms; Owner: -
--

CREATE SEQUENCE wms.receipt_items_receipt_item_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: receipt_items_receipt_item_id_seq; Type: SEQUENCE OWNED BY; Schema: wms; Owner: -
--

ALTER SEQUENCE wms.receipt_items_receipt_item_id_seq OWNED BY wms.receipt_items.receipt_item_id;


--
-- Name: task_items; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.task_items (
    item_id bigint NOT NULL,
    task_id bigint NOT NULL,
    product_id character varying(100) NOT NULL,
    quantity_planned numeric(15,3) NOT NULL,
    quantity_actual numeric(15,3),
    from_location_id bigint,
    batch_number character varying(50),
    discrepancy_reason text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE task_items; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON TABLE wms.task_items IS 'Товары в заявке с плановым и фактическим количеством';


--
-- Name: COLUMN task_items.quantity_planned; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.task_items.quantity_planned IS 'Запланированное количество (указывает менеджер)';


--
-- Name: COLUMN task_items.quantity_actual; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.task_items.quantity_actual IS 'Фактически перемещённое количество (вводит сотрудник)';


--
-- Name: COLUMN task_items.from_location_id; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.task_items.from_location_id IS 'Откуда фактически взяли товар (заполняется при выполнении)';


--
-- Name: COLUMN task_items.discrepancy_reason; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.task_items.discrepancy_reason IS 'Причина расхождения между planned и actual';


--
-- Name: task_items_item_id_seq; Type: SEQUENCE; Schema: wms; Owner: -
--

CREATE SEQUENCE wms.task_items_item_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: task_items_item_id_seq; Type: SEQUENCE OWNED BY; Schema: wms; Owner: -
--

ALTER SEQUENCE wms.task_items_item_id_seq OWNED BY wms.task_items.item_id;


--
-- Name: tasks; Type: TABLE; Schema: wms; Owner: -
--

CREATE TABLE wms.tasks (
    task_id bigint NOT NULL,
    task_type character varying(50) NOT NULL,
    status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    priority integer DEFAULT 5,
    from_location_id bigint,
    to_location_id bigint,
    assigned_to integer,
    assigned_at timestamp with time zone,
    due_date timestamp with time zone,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    reason text,
    notes text,
    related_movement_id bigint,
    parent_task_id bigint,
    metadata jsonb,
    created_by integer NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT check_task_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'assigned'::character varying, 'in_progress'::character varying, 'pending_approval'::character varying, 'pending_recount'::character varying, 'completed'::character varying, 'completed_with_discrepancy'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT check_task_type CHECK (((task_type)::text = ANY ((ARRAY['replenishment'::character varying, 'transfer'::character varying, 'picking'::character varying, 'putaway'::character varying, 'inventory'::character varying, 'discrepancy_approval'::character varying, 'recount'::character varying])::text[])))
);


--
-- Name: TABLE tasks; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON TABLE wms.tasks IS 'Внутренние заявки на перемещение товаров между зонами склада';


--
-- Name: COLUMN tasks.task_type; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.tasks.task_type IS 'Тип заявки: replenishment, transfer, picking, putaway, inventory, discrepancy_approval, recount';


--
-- Name: COLUMN tasks.status; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.tasks.status IS 'Статус жизненного цикла заявки';


--
-- Name: COLUMN tasks.assigned_to; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.tasks.assigned_to IS 'ID сотрудника из public.users, на которого назначена заявка';


--
-- Name: COLUMN tasks.parent_task_id; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.tasks.parent_task_id IS 'ID родительской заявки (для дочерних заявок на подтверждение расхождений)';


--
-- Name: COLUMN tasks.metadata; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.tasks.metadata IS 'Данные расхождений для служебных заявок (planned, actual, reason)';


--
-- Name: COLUMN tasks.created_by; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON COLUMN wms.tasks.created_by IS 'ID пользователя из public.users, создавшего заявку';


--
-- Name: tasks_task_id_seq; Type: SEQUENCE; Schema: wms; Owner: -
--

CREATE SEQUENCE wms.tasks_task_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tasks_task_id_seq; Type: SEQUENCE OWNED BY; Schema: wms; Owner: -
--

ALTER SEQUENCE wms.tasks_task_id_seq OWNED BY wms.tasks.task_id;


--
-- Name: v_container_contents_current; Type: VIEW; Schema: wms; Owner: -
--

CREATE VIEW wms.v_container_contents_current AS
 SELECT cc.content_id,
    cc.container_id,
    cc.product_id,
    cc.quantity,
    cc.batch_number,
    cc.is_scanned,
    cc.status,
    cc.created_at,
    cc.updated_at,
    c.qr_code,
    c.container_type,
    c.status AS container_status,
    p.name AS product_name
   FROM ((wms.container_contents cc
     JOIN wms.containers c ON ((cc.container_id = c.container_id)))
     JOIN public.products p ON (((cc.product_id)::text = (p.id)::text)))
  WHERE ((cc.status)::text = 'active'::text);


--
-- Name: VIEW v_container_contents_current; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON VIEW wms.v_container_contents_current IS 'Только актуальное (active) содержимое контейнеров. Используется для быстрых запросов без учёта истории (replaced/removed записей). Использование: SELECT * FROM wms.v_container_contents_current WHERE qr_code = ''QR-00001'';';


--
-- Name: v_container_details; Type: VIEW; Schema: wms; Owner: -
--

CREATE VIEW wms.v_container_details AS
 SELECT c.container_id,
    c.qr_code,
    c.container_type,
    c.status,
    l.location_code,
    l.zone_type,
    count(cc.content_id) AS products_count,
    sum(cc.quantity) AS total_units,
    json_agg(json_build_object('product_id', cc.product_id, 'quantity', cc.quantity, 'is_scanned', cc.is_scanned, 'batch_number', cc.batch_number)) FILTER (WHERE ((cc.status)::text = 'active'::text)) AS contents
   FROM ((wms.containers c
     LEFT JOIN wms.locations l ON ((c.location_id = l.location_id)))
     LEFT JOIN wms.container_contents cc ON ((c.container_id = cc.container_id)))
  WHERE (((cc.status)::text = 'active'::text) OR (cc.status IS NULL))
  GROUP BY c.container_id, c.qr_code, c.container_type, c.status, l.location_code, l.zone_type;


--
-- Name: VIEW v_container_details; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON VIEW wms.v_container_details IS 'Детальная информация о контейнерах с их содержимым в JSON формате. Показывает что лежит в каждом контейнере. Использование: SELECT * FROM wms.v_container_details WHERE qr_code = ''QR-00001'';';


--
-- Name: v_product_stock; Type: VIEW; Schema: wms; Owner: -
--

CREATE VIEW wms.v_product_stock AS
 SELECT p.id AS product_id,
    p.name AS product_name,
    p.category,
    COALESCE(sum(i.quantity), (0)::numeric) AS total_quantity,
    count(DISTINCT i.location_id) AS locations_count,
    COALESCE(sum(
        CASE
            WHEN (i.container_code IS NOT NULL) THEN i.quantity
            ELSE (0)::numeric
        END), (0)::numeric) AS in_containers,
    COALESCE(sum(
        CASE
            WHEN (i.container_code IS NULL) THEN i.quantity
            ELSE (0)::numeric
        END), (0)::numeric) AS loose,
    max(i.updated_at) AS last_updated
   FROM (public.products p
     LEFT JOIN wms.inventory i ON ((((p.id)::text = (i.product_id)::text) AND ((i.status)::text = 'available'::text))))
  GROUP BY p.id, p.name, p.category;


--
-- Name: VIEW v_product_stock; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON VIEW wms.v_product_stock IS 'Агрегированные остатки по товарам с разбивкой на контейнеры/россыпь. Показывает общее количество каждого товара на складе. Использование: SELECT * FROM wms.v_product_stock WHERE product_id = ''wild123'';';


--
-- Name: v_tasks_with_users; Type: VIEW; Schema: wms; Owner: -
--

CREATE VIEW wms.v_tasks_with_users AS
 SELECT t.task_id,
    t.task_type,
    t.status,
    t.priority,
    t.from_location_id,
    l_from.location_code AS from_location_code,
    l_from.zone_type AS from_zone_type,
    l_from.name AS from_location_name,
    t.to_location_id,
    l_to.location_code AS to_location_code,
    l_to.zone_type AS to_zone_type,
    l_to.name AS to_location_name,
    t.assigned_to,
    u_assigned.username AS assigned_username,
    u_assigned.full_name AS assigned_full_name,
    u_assigned.email AS assigned_email,
    t.created_by,
    u_created.username AS created_username,
    u_created.full_name AS created_full_name,
    t.due_date,
    t.created_at,
    t.assigned_at,
    t.started_at,
    t.completed_at,
    t.updated_at,
    t.reason,
    t.notes,
    t.metadata,
    t.related_movement_id,
    t.parent_task_id,
        CASE
            WHEN ((t.status)::text = 'pending'::text) THEN (EXTRACT(epoch FROM (now() - t.created_at)) / (60)::numeric)
            ELSE NULL::numeric
        END AS waiting_minutes,
        CASE
            WHEN ((t.status)::text = 'in_progress'::text) THEN (EXTRACT(epoch FROM (now() - t.started_at)) / (60)::numeric)
            ELSE NULL::numeric
        END AS execution_minutes,
        CASE
            WHEN ((t.completed_at IS NOT NULL) AND (t.started_at IS NOT NULL)) THEN (EXTRACT(epoch FROM (t.completed_at - t.started_at)) / (60)::numeric)
            ELSE NULL::numeric
        END AS total_execution_minutes,
        CASE
            WHEN ((t.task_type)::text = ANY ((ARRAY['discrepancy_approval'::character varying, 'recount'::character varying])::text[])) THEN (COALESCE(jsonb_array_length((t.metadata -> 'discrepancies'::text)), 0))::bigint
            ELSE ( SELECT count(*) AS count
               FROM wms.task_items
              WHERE (task_items.task_id = t.task_id))
        END AS items_count,
        CASE
            WHEN ((t.task_type)::text = ANY ((ARRAY['discrepancy_approval'::character varying, 'recount'::character varying])::text[])) THEN COALESCE(( SELECT sum(((item.value ->> 'quantity_planned'::text))::numeric) AS sum
               FROM jsonb_array_elements((t.metadata -> 'discrepancies'::text)) item(value)), (0)::numeric)
            ELSE COALESCE(( SELECT sum(task_items.quantity_planned) AS sum
               FROM wms.task_items
              WHERE (task_items.task_id = t.task_id)), (0)::numeric)
        END AS total_quantity_planned,
        CASE
            WHEN ((t.task_type)::text = ANY ((ARRAY['discrepancy_approval'::character varying, 'recount'::character varying])::text[])) THEN COALESCE(( SELECT sum(((item.value ->> 'quantity_actual'::text))::numeric) AS sum
               FROM jsonb_array_elements((t.metadata -> 'discrepancies'::text)) item(value)), (0)::numeric)
            ELSE COALESCE(( SELECT sum(task_items.quantity_actual) AS sum
               FROM wms.task_items
              WHERE (task_items.task_id = t.task_id)), (0)::numeric)
        END AS total_quantity_actual
   FROM ((((wms.tasks t
     LEFT JOIN wms.locations l_from ON ((t.from_location_id = l_from.location_id)))
     LEFT JOIN wms.locations l_to ON ((t.to_location_id = l_to.location_id)))
     LEFT JOIN public.users u_assigned ON ((t.assigned_to = u_assigned.id)))
     LEFT JOIN public.users u_created ON ((t.created_by = u_created.id)));


--
-- Name: VIEW v_tasks_with_users; Type: COMMENT; Schema: wms; Owner: -
--

COMMENT ON VIEW wms.v_tasks_with_users IS 'Заявки с подробной информацией о пользователях, локациях и товарах. Для дочерних заявок (discrepancy_approval, recount) данные о товарах берутся из metadata.discrepancies';


--
-- Name: movements_2026_01; Type: TABLE ATTACH; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ATTACH PARTITION wms.movements_2026_01 FOR VALUES FROM ('2025-12-31 21:00:00+00') TO ('2026-01-31 21:00:00+00');


--
-- Name: movements_2026_02; Type: TABLE ATTACH; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ATTACH PARTITION wms.movements_2026_02 FOR VALUES FROM ('2026-01-31 21:00:00+00') TO ('2026-02-28 21:00:00+00');


--
-- Name: movements_2026_03; Type: TABLE ATTACH; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ATTACH PARTITION wms.movements_2026_03 FOR VALUES FROM ('2026-02-28 21:00:00+00') TO ('2026-03-31 21:00:00+00');


--
-- Name: movements_2026_04; Type: TABLE ATTACH; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ATTACH PARTITION wms.movements_2026_04 FOR VALUES FROM ('2026-03-31 21:00:00+00') TO ('2026-04-30 21:00:00+00');


--
-- Name: movements_2026_05; Type: TABLE ATTACH; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ATTACH PARTITION wms.movements_2026_05 FOR VALUES FROM ('2026-04-30 21:00:00+00') TO ('2026-05-31 21:00:00+00');


--
-- Name: movements_2026_06; Type: TABLE ATTACH; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ATTACH PARTITION wms.movements_2026_06 FOR VALUES FROM ('2026-05-31 21:00:00+00') TO ('2026-06-30 21:00:00+00');


--
-- Name: movements_2026_07; Type: TABLE ATTACH; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ATTACH PARTITION wms.movements_2026_07 FOR VALUES FROM ('2026-06-30 21:00:00+00') TO ('2026-07-31 21:00:00+00');


--
-- Name: movements_2026_08; Type: TABLE ATTACH; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ATTACH PARTITION wms.movements_2026_08 FOR VALUES FROM ('2026-07-31 21:00:00+00') TO ('2026-08-31 21:00:00+00');


--
-- Name: movements_2026_09; Type: TABLE ATTACH; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ATTACH PARTITION wms.movements_2026_09 FOR VALUES FROM ('2026-08-31 21:00:00+00') TO ('2026-09-30 21:00:00+00');


--
-- Name: movements_2026_10; Type: TABLE ATTACH; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ATTACH PARTITION wms.movements_2026_10 FOR VALUES FROM ('2026-09-30 21:00:00+00') TO ('2026-10-31 21:00:00+00');


--
-- Name: movements_2026_11; Type: TABLE ATTACH; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ATTACH PARTITION wms.movements_2026_11 FOR VALUES FROM ('2026-10-31 21:00:00+00') TO ('2026-11-30 21:00:00+00');


--
-- Name: movements_2026_12; Type: TABLE ATTACH; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ATTACH PARTITION wms.movements_2026_12 FOR VALUES FROM ('2026-11-30 21:00:00+00') TO ('2026-12-31 21:00:00+00');


--
-- Name: container_contents content_id; Type: DEFAULT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.container_contents ALTER COLUMN content_id SET DEFAULT nextval('wms.container_contents_content_id_seq'::regclass);


--
-- Name: containers container_id; Type: DEFAULT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.containers ALTER COLUMN container_id SET DEFAULT nextval('wms.containers_container_id_seq'::regclass);


--
-- Name: fbs_shipment_items item_id; Type: DEFAULT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.fbs_shipment_items ALTER COLUMN item_id SET DEFAULT nextval('wms.fbs_shipment_items_item_id_seq'::regclass);


--
-- Name: fbs_shipments shipment_id; Type: DEFAULT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.fbs_shipments ALTER COLUMN shipment_id SET DEFAULT nextval('wms.fbs_shipments_shipment_id_seq'::regclass);


--
-- Name: inventory inventory_id; Type: DEFAULT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.inventory ALTER COLUMN inventory_id SET DEFAULT nextval('wms.inventory_inventory_id_seq'::regclass);


--
-- Name: inventory_snapshots snapshot_id; Type: DEFAULT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.inventory_snapshots ALTER COLUMN snapshot_id SET DEFAULT nextval('wms.inventory_snapshots_snapshot_id_seq'::regclass);


--
-- Name: locations location_id; Type: DEFAULT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.locations ALTER COLUMN location_id SET DEFAULT nextval('wms.locations_location_id_seq'::regclass);


--
-- Name: movements movement_id; Type: DEFAULT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.movements ALTER COLUMN movement_id SET DEFAULT nextval('wms.movements_movement_id_seq'::regclass);


--
-- Name: notifications notification_id; Type: DEFAULT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.notifications ALTER COLUMN notification_id SET DEFAULT nextval('wms.notifications_notification_id_seq'::regclass);


--
-- Name: receipt_items receipt_item_id; Type: DEFAULT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.receipt_items ALTER COLUMN receipt_item_id SET DEFAULT nextval('wms.receipt_items_receipt_item_id_seq'::regclass);


--
-- Name: task_items item_id; Type: DEFAULT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.task_items ALTER COLUMN item_id SET DEFAULT nextval('wms.task_items_item_id_seq'::regclass);


--
-- Name: tasks task_id; Type: DEFAULT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.tasks ALTER COLUMN task_id SET DEFAULT nextval('wms.tasks_task_id_seq'::regclass);


--
-- Name: container_contents container_contents_pkey; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.container_contents
    ADD CONSTRAINT container_contents_pkey PRIMARY KEY (content_id);


--
-- Name: containers containers_pkey; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.containers
    ADD CONSTRAINT containers_pkey PRIMARY KEY (container_id);


--
-- Name: containers containers_qr_code_key; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.containers
    ADD CONSTRAINT containers_qr_code_key UNIQUE (qr_code);


--
-- Name: fbs_shipment_items fbs_shipment_items_pkey; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.fbs_shipment_items
    ADD CONSTRAINT fbs_shipment_items_pkey PRIMARY KEY (item_id);


--
-- Name: fbs_shipments fbs_shipments_pkey; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.fbs_shipments
    ADD CONSTRAINT fbs_shipments_pkey PRIMARY KEY (shipment_id);


--
-- Name: inventory inventory_pkey; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.inventory
    ADD CONSTRAINT inventory_pkey PRIMARY KEY (inventory_id);


--
-- Name: inventory_snapshots inventory_snapshots_pkey; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.inventory_snapshots
    ADD CONSTRAINT inventory_snapshots_pkey PRIMARY KEY (snapshot_id);


--
-- Name: locations locations_location_code_key; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.locations
    ADD CONSTRAINT locations_location_code_key UNIQUE (location_code);


--
-- Name: locations locations_pkey; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.locations
    ADD CONSTRAINT locations_pkey PRIMARY KEY (location_id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (notification_id);


--
-- Name: receipt_items receipt_items_pkey; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.receipt_items
    ADD CONSTRAINT receipt_items_pkey PRIMARY KEY (receipt_item_id);


--
-- Name: task_items task_items_pkey; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.task_items
    ADD CONSTRAINT task_items_pkey PRIMARY KEY (item_id);


--
-- Name: tasks tasks_pkey; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (task_id);


--
-- Name: container_contents uq_container_content; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.container_contents
    ADD CONSTRAINT uq_container_content UNIQUE (container_id, product_id, batch_number, status);


--
-- Name: inventory uq_inventory; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.inventory
    ADD CONSTRAINT uq_inventory UNIQUE NULLS NOT DISTINCT (product_id, location_id, status, batch_number, container_code);


--
-- Name: receipt_items uq_receipt_guid_product; Type: CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.receipt_items
    ADD CONSTRAINT uq_receipt_guid_product UNIQUE (guid, product_id);


--
-- Name: idx_containers_location; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_containers_location ON wms.containers USING btree (location_id);


--
-- Name: idx_containers_parent; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_containers_parent ON wms.containers USING btree (parent_container_id);


--
-- Name: idx_containers_qr; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_containers_qr ON wms.containers USING btree (qr_code);


--
-- Name: idx_containers_status; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_containers_status ON wms.containers USING btree (status) WHERE ((status)::text <> 'empty'::text);


--
-- Name: idx_containers_type; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_containers_type ON wms.containers USING btree (container_type);


--
-- Name: idx_content_batch; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_content_batch ON wms.container_contents USING btree (batch_number);


--
-- Name: idx_content_container; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_content_container ON wms.container_contents USING btree (container_id);


--
-- Name: idx_content_product; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_content_product ON wms.container_contents USING btree (product_id);


--
-- Name: idx_content_status; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_content_status ON wms.container_contents USING btree (status) WHERE ((status)::text = 'active'::text);


--
-- Name: idx_fbs_shipment_items_next_retry; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_fbs_shipment_items_next_retry ON wms.fbs_shipment_items USING btree (next_retry_at) WHERE ((status)::text = 'pending_retry'::text);


--
-- Name: idx_fbs_shipment_items_shipment_id; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_fbs_shipment_items_shipment_id ON wms.fbs_shipment_items USING btree (shipment_id);


--
-- Name: idx_fbs_shipment_items_status; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_fbs_shipment_items_status ON wms.fbs_shipment_items USING btree (status);


CREATE INDEX idx_fbs_shipments_source_received_at ON wms.fbs_shipments USING btree (source, received_at DESC);

CREATE INDEX idx_fbs_shipments_source_status ON wms.fbs_shipments USING btree (source, status);


--
-- Name: idx_inventory_batch; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_inventory_batch ON wms.inventory USING btree (batch_number);


--
-- Name: idx_inventory_container; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_inventory_container ON wms.inventory USING btree (container_code) WHERE (container_code IS NOT NULL);


--
-- Name: idx_inventory_location; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_inventory_location ON wms.inventory USING btree (location_id);


--
-- Name: idx_inventory_product; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_inventory_product ON wms.inventory USING btree (product_id);


--
-- Name: idx_inventory_product_location; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_inventory_product_location ON wms.inventory USING btree (product_id, location_id);


--
-- Name: idx_inventory_status; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_inventory_status ON wms.inventory USING btree (status);


--
-- Name: idx_locations_active; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_locations_active ON wms.locations USING btree (is_active) WHERE (is_active = true);


--
-- Name: idx_locations_code; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_locations_code ON wms.locations USING btree (location_code);


--
-- Name: idx_locations_parent; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_locations_parent ON wms.locations USING btree (parent_location_id);


--
-- Name: idx_locations_path; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_locations_path ON wms.locations USING gist (path);


--
-- Name: idx_locations_zone_type; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_locations_zone_type ON wms.locations USING btree (zone_type) WHERE (zone_type IS NOT NULL);


--
-- Name: idx_movements_container; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_movements_container ON ONLY wms.movements USING btree (container_code);


--
-- Name: idx_movements_created; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_movements_created ON ONLY wms.movements USING btree (created_at);


--
-- Name: idx_movements_from_location; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_movements_from_location ON ONLY wms.movements USING btree (from_location_id);


--
-- Name: idx_movements_product; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_movements_product ON ONLY wms.movements USING btree (product_id);


--
-- Name: idx_movements_product_created; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_movements_product_created ON ONLY wms.movements USING btree (product_id, created_at);


--
-- Name: idx_movements_to_location; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_movements_to_location ON ONLY wms.movements USING btree (to_location_id);


--
-- Name: idx_movements_type; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_movements_type ON ONLY wms.movements USING btree (movement_type);


--
-- Name: idx_mv_product_stock_product; Type: INDEX; Schema: wms; Owner: -
--

CREATE UNIQUE INDEX idx_mv_product_stock_product ON wms.mv_product_stock USING btree (product_id);


--
-- Name: idx_notifications_created; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_notifications_created ON wms.notifications USING btree (created_at DESC);


--
-- Name: idx_notifications_type; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_notifications_type ON wms.notifications USING btree (notification_type);


--
-- Name: idx_notifications_user; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_notifications_user ON wms.notifications USING btree (user_id, is_read);


--
-- Name: idx_receipt_items_guid; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_receipt_items_guid ON wms.receipt_items USING btree (guid);


--
-- Name: idx_receipt_items_product; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_receipt_items_product ON wms.receipt_items USING btree (product_id);


--
-- Name: idx_receipt_items_supplier; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_receipt_items_supplier ON wms.receipt_items USING btree (supplier_code) WHERE (supplier_code IS NOT NULL);


--
-- Name: idx_snapshots_date_product; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_snapshots_date_product ON wms.inventory_snapshots USING btree (snapshot_date, product_id);


--
-- Name: idx_snapshots_location; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_snapshots_location ON wms.inventory_snapshots USING btree (location_id);


--
-- Name: idx_snapshots_product; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_snapshots_product ON wms.inventory_snapshots USING btree (product_id);


--
-- Name: idx_task_items_from_location; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_task_items_from_location ON wms.task_items USING btree (from_location_id) WHERE (from_location_id IS NOT NULL);


--
-- Name: idx_task_items_product; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_task_items_product ON wms.task_items USING btree (product_id);


--
-- Name: idx_task_items_task; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_task_items_task ON wms.task_items USING btree (task_id);


--
-- Name: idx_tasks_assigned; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_tasks_assigned ON wms.tasks USING btree (assigned_to) WHERE (assigned_to IS NOT NULL);


--
-- Name: idx_tasks_created_by; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_tasks_created_by ON wms.tasks USING btree (created_by);


--
-- Name: idx_tasks_from_location; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_tasks_from_location ON wms.tasks USING btree (from_location_id);


--
-- Name: idx_tasks_parent; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_tasks_parent ON wms.tasks USING btree (parent_task_id) WHERE (parent_task_id IS NOT NULL);


--
-- Name: idx_tasks_priority; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_tasks_priority ON wms.tasks USING btree (priority, created_at) WHERE ((status)::text = 'pending'::text);


--
-- Name: idx_tasks_status; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_tasks_status ON wms.tasks USING btree (status) WHERE ((status)::text <> ALL ((ARRAY['completed'::character varying, 'completed_with_discrepancy'::character varying, 'cancelled'::character varying])::text[]));


--
-- Name: idx_tasks_to_location; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX idx_tasks_to_location ON wms.tasks USING btree (to_location_id);


--
-- Name: movements_2026_01_container_code_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_01_container_code_idx ON wms.movements_2026_01 USING btree (container_code);


--
-- Name: movements_2026_01_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_01_created_at_idx ON wms.movements_2026_01 USING btree (created_at);


--
-- Name: movements_2026_01_from_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_01_from_location_id_idx ON wms.movements_2026_01 USING btree (from_location_id);


--
-- Name: movements_2026_01_movement_type_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_01_movement_type_idx ON wms.movements_2026_01 USING btree (movement_type);


--
-- Name: movements_2026_01_product_id_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_01_product_id_created_at_idx ON wms.movements_2026_01 USING btree (product_id, created_at);


--
-- Name: movements_2026_01_product_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_01_product_id_idx ON wms.movements_2026_01 USING btree (product_id);


--
-- Name: movements_2026_01_to_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_01_to_location_id_idx ON wms.movements_2026_01 USING btree (to_location_id);


--
-- Name: movements_2026_02_container_code_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_02_container_code_idx ON wms.movements_2026_02 USING btree (container_code);


--
-- Name: movements_2026_02_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_02_created_at_idx ON wms.movements_2026_02 USING btree (created_at);


--
-- Name: movements_2026_02_from_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_02_from_location_id_idx ON wms.movements_2026_02 USING btree (from_location_id);


--
-- Name: movements_2026_02_movement_type_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_02_movement_type_idx ON wms.movements_2026_02 USING btree (movement_type);


--
-- Name: movements_2026_02_product_id_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_02_product_id_created_at_idx ON wms.movements_2026_02 USING btree (product_id, created_at);


--
-- Name: movements_2026_02_product_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_02_product_id_idx ON wms.movements_2026_02 USING btree (product_id);


--
-- Name: movements_2026_02_to_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_02_to_location_id_idx ON wms.movements_2026_02 USING btree (to_location_id);


--
-- Name: movements_2026_03_container_code_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_03_container_code_idx ON wms.movements_2026_03 USING btree (container_code);


--
-- Name: movements_2026_03_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_03_created_at_idx ON wms.movements_2026_03 USING btree (created_at);


--
-- Name: movements_2026_03_from_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_03_from_location_id_idx ON wms.movements_2026_03 USING btree (from_location_id);


--
-- Name: movements_2026_03_movement_type_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_03_movement_type_idx ON wms.movements_2026_03 USING btree (movement_type);


--
-- Name: movements_2026_03_product_id_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_03_product_id_created_at_idx ON wms.movements_2026_03 USING btree (product_id, created_at);


--
-- Name: movements_2026_03_product_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_03_product_id_idx ON wms.movements_2026_03 USING btree (product_id);


--
-- Name: movements_2026_03_to_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_03_to_location_id_idx ON wms.movements_2026_03 USING btree (to_location_id);


--
-- Name: movements_2026_04_container_code_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_04_container_code_idx ON wms.movements_2026_04 USING btree (container_code);


--
-- Name: movements_2026_04_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_04_created_at_idx ON wms.movements_2026_04 USING btree (created_at);


--
-- Name: movements_2026_04_from_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_04_from_location_id_idx ON wms.movements_2026_04 USING btree (from_location_id);


--
-- Name: movements_2026_04_movement_type_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_04_movement_type_idx ON wms.movements_2026_04 USING btree (movement_type);


--
-- Name: movements_2026_04_product_id_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_04_product_id_created_at_idx ON wms.movements_2026_04 USING btree (product_id, created_at);


--
-- Name: movements_2026_04_product_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_04_product_id_idx ON wms.movements_2026_04 USING btree (product_id);


--
-- Name: movements_2026_04_to_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_04_to_location_id_idx ON wms.movements_2026_04 USING btree (to_location_id);


--
-- Name: movements_2026_05_container_code_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_05_container_code_idx ON wms.movements_2026_05 USING btree (container_code);


--
-- Name: movements_2026_05_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_05_created_at_idx ON wms.movements_2026_05 USING btree (created_at);


--
-- Name: movements_2026_05_from_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_05_from_location_id_idx ON wms.movements_2026_05 USING btree (from_location_id);


--
-- Name: movements_2026_05_movement_type_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_05_movement_type_idx ON wms.movements_2026_05 USING btree (movement_type);


--
-- Name: movements_2026_05_product_id_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_05_product_id_created_at_idx ON wms.movements_2026_05 USING btree (product_id, created_at);


--
-- Name: movements_2026_05_product_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_05_product_id_idx ON wms.movements_2026_05 USING btree (product_id);


--
-- Name: movements_2026_05_to_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_05_to_location_id_idx ON wms.movements_2026_05 USING btree (to_location_id);


--
-- Name: movements_2026_06_container_code_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_06_container_code_idx ON wms.movements_2026_06 USING btree (container_code);


--
-- Name: movements_2026_06_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_06_created_at_idx ON wms.movements_2026_06 USING btree (created_at);


--
-- Name: movements_2026_06_from_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_06_from_location_id_idx ON wms.movements_2026_06 USING btree (from_location_id);


--
-- Name: movements_2026_06_movement_type_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_06_movement_type_idx ON wms.movements_2026_06 USING btree (movement_type);


--
-- Name: movements_2026_06_product_id_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_06_product_id_created_at_idx ON wms.movements_2026_06 USING btree (product_id, created_at);


--
-- Name: movements_2026_06_product_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_06_product_id_idx ON wms.movements_2026_06 USING btree (product_id);


--
-- Name: movements_2026_06_to_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_06_to_location_id_idx ON wms.movements_2026_06 USING btree (to_location_id);


--
-- Name: movements_2026_07_container_code_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_07_container_code_idx ON wms.movements_2026_07 USING btree (container_code);


--
-- Name: movements_2026_07_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_07_created_at_idx ON wms.movements_2026_07 USING btree (created_at);


--
-- Name: movements_2026_07_from_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_07_from_location_id_idx ON wms.movements_2026_07 USING btree (from_location_id);


--
-- Name: movements_2026_07_movement_type_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_07_movement_type_idx ON wms.movements_2026_07 USING btree (movement_type);


--
-- Name: movements_2026_07_product_id_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_07_product_id_created_at_idx ON wms.movements_2026_07 USING btree (product_id, created_at);


--
-- Name: movements_2026_07_product_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_07_product_id_idx ON wms.movements_2026_07 USING btree (product_id);


--
-- Name: movements_2026_07_to_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_07_to_location_id_idx ON wms.movements_2026_07 USING btree (to_location_id);


--
-- Name: movements_2026_08_container_code_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_08_container_code_idx ON wms.movements_2026_08 USING btree (container_code);


--
-- Name: movements_2026_08_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_08_created_at_idx ON wms.movements_2026_08 USING btree (created_at);


--
-- Name: movements_2026_08_from_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_08_from_location_id_idx ON wms.movements_2026_08 USING btree (from_location_id);


--
-- Name: movements_2026_08_movement_type_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_08_movement_type_idx ON wms.movements_2026_08 USING btree (movement_type);


--
-- Name: movements_2026_08_product_id_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_08_product_id_created_at_idx ON wms.movements_2026_08 USING btree (product_id, created_at);


--
-- Name: movements_2026_08_product_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_08_product_id_idx ON wms.movements_2026_08 USING btree (product_id);


--
-- Name: movements_2026_08_to_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_08_to_location_id_idx ON wms.movements_2026_08 USING btree (to_location_id);


--
-- Name: movements_2026_09_container_code_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_09_container_code_idx ON wms.movements_2026_09 USING btree (container_code);


--
-- Name: movements_2026_09_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_09_created_at_idx ON wms.movements_2026_09 USING btree (created_at);


--
-- Name: movements_2026_09_from_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_09_from_location_id_idx ON wms.movements_2026_09 USING btree (from_location_id);


--
-- Name: movements_2026_09_movement_type_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_09_movement_type_idx ON wms.movements_2026_09 USING btree (movement_type);


--
-- Name: movements_2026_09_product_id_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_09_product_id_created_at_idx ON wms.movements_2026_09 USING btree (product_id, created_at);


--
-- Name: movements_2026_09_product_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_09_product_id_idx ON wms.movements_2026_09 USING btree (product_id);


--
-- Name: movements_2026_09_to_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_09_to_location_id_idx ON wms.movements_2026_09 USING btree (to_location_id);


--
-- Name: movements_2026_10_container_code_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_10_container_code_idx ON wms.movements_2026_10 USING btree (container_code);


--
-- Name: movements_2026_10_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_10_created_at_idx ON wms.movements_2026_10 USING btree (created_at);


--
-- Name: movements_2026_10_from_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_10_from_location_id_idx ON wms.movements_2026_10 USING btree (from_location_id);


--
-- Name: movements_2026_10_movement_type_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_10_movement_type_idx ON wms.movements_2026_10 USING btree (movement_type);


--
-- Name: movements_2026_10_product_id_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_10_product_id_created_at_idx ON wms.movements_2026_10 USING btree (product_id, created_at);


--
-- Name: movements_2026_10_product_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_10_product_id_idx ON wms.movements_2026_10 USING btree (product_id);


--
-- Name: movements_2026_10_to_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_10_to_location_id_idx ON wms.movements_2026_10 USING btree (to_location_id);


--
-- Name: movements_2026_11_container_code_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_11_container_code_idx ON wms.movements_2026_11 USING btree (container_code);


--
-- Name: movements_2026_11_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_11_created_at_idx ON wms.movements_2026_11 USING btree (created_at);


--
-- Name: movements_2026_11_from_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_11_from_location_id_idx ON wms.movements_2026_11 USING btree (from_location_id);


--
-- Name: movements_2026_11_movement_type_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_11_movement_type_idx ON wms.movements_2026_11 USING btree (movement_type);


--
-- Name: movements_2026_11_product_id_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_11_product_id_created_at_idx ON wms.movements_2026_11 USING btree (product_id, created_at);


--
-- Name: movements_2026_11_product_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_11_product_id_idx ON wms.movements_2026_11 USING btree (product_id);


--
-- Name: movements_2026_11_to_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_11_to_location_id_idx ON wms.movements_2026_11 USING btree (to_location_id);


--
-- Name: movements_2026_12_container_code_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_12_container_code_idx ON wms.movements_2026_12 USING btree (container_code);


--
-- Name: movements_2026_12_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_12_created_at_idx ON wms.movements_2026_12 USING btree (created_at);


--
-- Name: movements_2026_12_from_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_12_from_location_id_idx ON wms.movements_2026_12 USING btree (from_location_id);


--
-- Name: movements_2026_12_movement_type_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_12_movement_type_idx ON wms.movements_2026_12 USING btree (movement_type);


--
-- Name: movements_2026_12_product_id_created_at_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_12_product_id_created_at_idx ON wms.movements_2026_12 USING btree (product_id, created_at);


--
-- Name: movements_2026_12_product_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_12_product_id_idx ON wms.movements_2026_12 USING btree (product_id);


--
-- Name: movements_2026_12_to_location_id_idx; Type: INDEX; Schema: wms; Owner: -
--

CREATE INDEX movements_2026_12_to_location_id_idx ON wms.movements_2026_12 USING btree (to_location_id);


--
-- Name: movements_2026_01_container_code_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_container ATTACH PARTITION wms.movements_2026_01_container_code_idx;


--
-- Name: movements_2026_01_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_created ATTACH PARTITION wms.movements_2026_01_created_at_idx;


--
-- Name: movements_2026_01_from_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_from_location ATTACH PARTITION wms.movements_2026_01_from_location_id_idx;


--
-- Name: movements_2026_01_movement_type_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_type ATTACH PARTITION wms.movements_2026_01_movement_type_idx;


--
-- Name: movements_2026_01_product_id_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product_created ATTACH PARTITION wms.movements_2026_01_product_id_created_at_idx;


--
-- Name: movements_2026_01_product_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product ATTACH PARTITION wms.movements_2026_01_product_id_idx;


--
-- Name: movements_2026_01_to_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_to_location ATTACH PARTITION wms.movements_2026_01_to_location_id_idx;


--
-- Name: movements_2026_02_container_code_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_container ATTACH PARTITION wms.movements_2026_02_container_code_idx;


--
-- Name: movements_2026_02_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_created ATTACH PARTITION wms.movements_2026_02_created_at_idx;


--
-- Name: movements_2026_02_from_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_from_location ATTACH PARTITION wms.movements_2026_02_from_location_id_idx;


--
-- Name: movements_2026_02_movement_type_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_type ATTACH PARTITION wms.movements_2026_02_movement_type_idx;


--
-- Name: movements_2026_02_product_id_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product_created ATTACH PARTITION wms.movements_2026_02_product_id_created_at_idx;


--
-- Name: movements_2026_02_product_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product ATTACH PARTITION wms.movements_2026_02_product_id_idx;


--
-- Name: movements_2026_02_to_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_to_location ATTACH PARTITION wms.movements_2026_02_to_location_id_idx;


--
-- Name: movements_2026_03_container_code_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_container ATTACH PARTITION wms.movements_2026_03_container_code_idx;


--
-- Name: movements_2026_03_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_created ATTACH PARTITION wms.movements_2026_03_created_at_idx;


--
-- Name: movements_2026_03_from_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_from_location ATTACH PARTITION wms.movements_2026_03_from_location_id_idx;


--
-- Name: movements_2026_03_movement_type_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_type ATTACH PARTITION wms.movements_2026_03_movement_type_idx;


--
-- Name: movements_2026_03_product_id_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product_created ATTACH PARTITION wms.movements_2026_03_product_id_created_at_idx;


--
-- Name: movements_2026_03_product_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product ATTACH PARTITION wms.movements_2026_03_product_id_idx;


--
-- Name: movements_2026_03_to_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_to_location ATTACH PARTITION wms.movements_2026_03_to_location_id_idx;


--
-- Name: movements_2026_04_container_code_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_container ATTACH PARTITION wms.movements_2026_04_container_code_idx;


--
-- Name: movements_2026_04_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_created ATTACH PARTITION wms.movements_2026_04_created_at_idx;


--
-- Name: movements_2026_04_from_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_from_location ATTACH PARTITION wms.movements_2026_04_from_location_id_idx;


--
-- Name: movements_2026_04_movement_type_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_type ATTACH PARTITION wms.movements_2026_04_movement_type_idx;


--
-- Name: movements_2026_04_product_id_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product_created ATTACH PARTITION wms.movements_2026_04_product_id_created_at_idx;


--
-- Name: movements_2026_04_product_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product ATTACH PARTITION wms.movements_2026_04_product_id_idx;


--
-- Name: movements_2026_04_to_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_to_location ATTACH PARTITION wms.movements_2026_04_to_location_id_idx;


--
-- Name: movements_2026_05_container_code_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_container ATTACH PARTITION wms.movements_2026_05_container_code_idx;


--
-- Name: movements_2026_05_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_created ATTACH PARTITION wms.movements_2026_05_created_at_idx;


--
-- Name: movements_2026_05_from_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_from_location ATTACH PARTITION wms.movements_2026_05_from_location_id_idx;


--
-- Name: movements_2026_05_movement_type_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_type ATTACH PARTITION wms.movements_2026_05_movement_type_idx;


--
-- Name: movements_2026_05_product_id_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product_created ATTACH PARTITION wms.movements_2026_05_product_id_created_at_idx;


--
-- Name: movements_2026_05_product_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product ATTACH PARTITION wms.movements_2026_05_product_id_idx;


--
-- Name: movements_2026_05_to_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_to_location ATTACH PARTITION wms.movements_2026_05_to_location_id_idx;


--
-- Name: movements_2026_06_container_code_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_container ATTACH PARTITION wms.movements_2026_06_container_code_idx;


--
-- Name: movements_2026_06_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_created ATTACH PARTITION wms.movements_2026_06_created_at_idx;


--
-- Name: movements_2026_06_from_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_from_location ATTACH PARTITION wms.movements_2026_06_from_location_id_idx;


--
-- Name: movements_2026_06_movement_type_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_type ATTACH PARTITION wms.movements_2026_06_movement_type_idx;


--
-- Name: movements_2026_06_product_id_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product_created ATTACH PARTITION wms.movements_2026_06_product_id_created_at_idx;


--
-- Name: movements_2026_06_product_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product ATTACH PARTITION wms.movements_2026_06_product_id_idx;


--
-- Name: movements_2026_06_to_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_to_location ATTACH PARTITION wms.movements_2026_06_to_location_id_idx;


--
-- Name: movements_2026_07_container_code_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_container ATTACH PARTITION wms.movements_2026_07_container_code_idx;


--
-- Name: movements_2026_07_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_created ATTACH PARTITION wms.movements_2026_07_created_at_idx;


--
-- Name: movements_2026_07_from_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_from_location ATTACH PARTITION wms.movements_2026_07_from_location_id_idx;


--
-- Name: movements_2026_07_movement_type_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_type ATTACH PARTITION wms.movements_2026_07_movement_type_idx;


--
-- Name: movements_2026_07_product_id_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product_created ATTACH PARTITION wms.movements_2026_07_product_id_created_at_idx;


--
-- Name: movements_2026_07_product_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product ATTACH PARTITION wms.movements_2026_07_product_id_idx;


--
-- Name: movements_2026_07_to_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_to_location ATTACH PARTITION wms.movements_2026_07_to_location_id_idx;


--
-- Name: movements_2026_08_container_code_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_container ATTACH PARTITION wms.movements_2026_08_container_code_idx;


--
-- Name: movements_2026_08_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_created ATTACH PARTITION wms.movements_2026_08_created_at_idx;


--
-- Name: movements_2026_08_from_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_from_location ATTACH PARTITION wms.movements_2026_08_from_location_id_idx;


--
-- Name: movements_2026_08_movement_type_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_type ATTACH PARTITION wms.movements_2026_08_movement_type_idx;


--
-- Name: movements_2026_08_product_id_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product_created ATTACH PARTITION wms.movements_2026_08_product_id_created_at_idx;


--
-- Name: movements_2026_08_product_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product ATTACH PARTITION wms.movements_2026_08_product_id_idx;


--
-- Name: movements_2026_08_to_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_to_location ATTACH PARTITION wms.movements_2026_08_to_location_id_idx;


--
-- Name: movements_2026_09_container_code_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_container ATTACH PARTITION wms.movements_2026_09_container_code_idx;


--
-- Name: movements_2026_09_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_created ATTACH PARTITION wms.movements_2026_09_created_at_idx;


--
-- Name: movements_2026_09_from_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_from_location ATTACH PARTITION wms.movements_2026_09_from_location_id_idx;


--
-- Name: movements_2026_09_movement_type_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_type ATTACH PARTITION wms.movements_2026_09_movement_type_idx;


--
-- Name: movements_2026_09_product_id_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product_created ATTACH PARTITION wms.movements_2026_09_product_id_created_at_idx;


--
-- Name: movements_2026_09_product_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product ATTACH PARTITION wms.movements_2026_09_product_id_idx;


--
-- Name: movements_2026_09_to_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_to_location ATTACH PARTITION wms.movements_2026_09_to_location_id_idx;


--
-- Name: movements_2026_10_container_code_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_container ATTACH PARTITION wms.movements_2026_10_container_code_idx;


--
-- Name: movements_2026_10_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_created ATTACH PARTITION wms.movements_2026_10_created_at_idx;


--
-- Name: movements_2026_10_from_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_from_location ATTACH PARTITION wms.movements_2026_10_from_location_id_idx;


--
-- Name: movements_2026_10_movement_type_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_type ATTACH PARTITION wms.movements_2026_10_movement_type_idx;


--
-- Name: movements_2026_10_product_id_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product_created ATTACH PARTITION wms.movements_2026_10_product_id_created_at_idx;


--
-- Name: movements_2026_10_product_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product ATTACH PARTITION wms.movements_2026_10_product_id_idx;


--
-- Name: movements_2026_10_to_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_to_location ATTACH PARTITION wms.movements_2026_10_to_location_id_idx;


--
-- Name: movements_2026_11_container_code_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_container ATTACH PARTITION wms.movements_2026_11_container_code_idx;


--
-- Name: movements_2026_11_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_created ATTACH PARTITION wms.movements_2026_11_created_at_idx;


--
-- Name: movements_2026_11_from_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_from_location ATTACH PARTITION wms.movements_2026_11_from_location_id_idx;


--
-- Name: movements_2026_11_movement_type_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_type ATTACH PARTITION wms.movements_2026_11_movement_type_idx;


--
-- Name: movements_2026_11_product_id_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product_created ATTACH PARTITION wms.movements_2026_11_product_id_created_at_idx;


--
-- Name: movements_2026_11_product_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product ATTACH PARTITION wms.movements_2026_11_product_id_idx;


--
-- Name: movements_2026_11_to_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_to_location ATTACH PARTITION wms.movements_2026_11_to_location_id_idx;


--
-- Name: movements_2026_12_container_code_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_container ATTACH PARTITION wms.movements_2026_12_container_code_idx;


--
-- Name: movements_2026_12_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_created ATTACH PARTITION wms.movements_2026_12_created_at_idx;


--
-- Name: movements_2026_12_from_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_from_location ATTACH PARTITION wms.movements_2026_12_from_location_id_idx;


--
-- Name: movements_2026_12_movement_type_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_type ATTACH PARTITION wms.movements_2026_12_movement_type_idx;


--
-- Name: movements_2026_12_product_id_created_at_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product_created ATTACH PARTITION wms.movements_2026_12_product_id_created_at_idx;


--
-- Name: movements_2026_12_product_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_product ATTACH PARTITION wms.movements_2026_12_product_id_idx;


--
-- Name: movements_2026_12_to_location_id_idx; Type: INDEX ATTACH; Schema: wms; Owner: -
--

ALTER INDEX wms.idx_movements_to_location ATTACH PARTITION wms.movements_2026_12_to_location_id_idx;


--
-- Name: containers trg_containers_updated_at; Type: TRIGGER; Schema: wms; Owner: -
--

CREATE TRIGGER trg_containers_updated_at BEFORE UPDATE ON wms.containers FOR EACH ROW EXECUTE FUNCTION wms.update_containers_timestamp();


--
-- Name: fbs_shipment_items trg_fbs_item_updated_at; Type: TRIGGER; Schema: wms; Owner: -
--

CREATE TRIGGER trg_fbs_item_updated_at BEFORE UPDATE ON wms.fbs_shipment_items FOR EACH ROW EXECUTE FUNCTION wms.update_fbs_item_updated_at();


--
-- Name: locations trg_generate_location_code; Type: TRIGGER; Schema: wms; Owner: -
--

CREATE TRIGGER trg_generate_location_code BEFORE INSERT ON wms.locations FOR EACH ROW EXECUTE FUNCTION wms.generate_location_code();


--
-- Name: locations trg_generate_location_path; Type: TRIGGER; Schema: wms; Owner: -
--

CREATE TRIGGER trg_generate_location_path BEFORE INSERT OR UPDATE OF parent_location_id ON wms.locations FOR EACH ROW EXECUTE FUNCTION wms.generate_location_path();


--
-- Name: inventory trg_inventory_updated_at; Type: TRIGGER; Schema: wms; Owner: -
--

CREATE TRIGGER trg_inventory_updated_at BEFORE UPDATE ON wms.inventory FOR EACH ROW EXECUTE FUNCTION wms.update_inventory_timestamp();


--
-- Name: locations trg_locations_updated_at; Type: TRIGGER; Schema: wms; Owner: -
--

CREATE TRIGGER trg_locations_updated_at BEFORE UPDATE ON wms.locations FOR EACH ROW EXECUTE FUNCTION wms.update_locations_timestamp();


--
-- Name: containers trg_move_container_inventory; Type: TRIGGER; Schema: wms; Owner: -
--

CREATE TRIGGER trg_move_container_inventory AFTER UPDATE OF location_id ON wms.containers FOR EACH ROW EXECUTE FUNCTION wms.move_container_inventory();


--
-- Name: receipt_items trg_receipt_items_updated_at; Type: TRIGGER; Schema: wms; Owner: -
--

CREATE TRIGGER trg_receipt_items_updated_at BEFORE UPDATE ON wms.receipt_items FOR EACH ROW EXECUTE FUNCTION wms.update_inventory_timestamp();


--
-- Name: container_contents trg_sync_container_contents_to_inventory; Type: TRIGGER; Schema: wms; Owner: -
--

CREATE TRIGGER trg_sync_container_contents_to_inventory AFTER INSERT ON wms.container_contents FOR EACH ROW EXECUTE FUNCTION wms.sync_container_to_inventory();


--
-- Name: tasks trg_tasks_updated_at; Type: TRIGGER; Schema: wms; Owner: -
--

CREATE TRIGGER trg_tasks_updated_at BEFORE UPDATE ON wms.tasks FOR EACH ROW EXECUTE FUNCTION wms.update_updated_at_column();


--
-- Name: movements trg_update_inventory_from_movement; Type: TRIGGER; Schema: wms; Owner: -
--

CREATE TRIGGER trg_update_inventory_from_movement AFTER INSERT ON wms.movements FOR EACH ROW EXECUTE FUNCTION wms.update_inventory_from_movement();


--
-- Name: fbs_shipment_items fbs_shipment_items_shipment_id_fkey; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.fbs_shipment_items
    ADD CONSTRAINT fbs_shipment_items_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES wms.fbs_shipments(shipment_id) ON DELETE CASCADE;


--
-- Name: containers fk_container_location; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.containers
    ADD CONSTRAINT fk_container_location FOREIGN KEY (location_id) REFERENCES wms.locations(location_id) ON DELETE RESTRICT;


--
-- Name: container_contents fk_content_container; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.container_contents
    ADD CONSTRAINT fk_content_container FOREIGN KEY (container_id) REFERENCES wms.containers(container_id) ON DELETE CASCADE;


--
-- Name: container_contents fk_content_product; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.container_contents
    ADD CONSTRAINT fk_content_product FOREIGN KEY (product_id) REFERENCES public.products(id) ON DELETE RESTRICT;


--
-- Name: inventory fk_inventory_location; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.inventory
    ADD CONSTRAINT fk_inventory_location FOREIGN KEY (location_id) REFERENCES wms.locations(location_id) ON DELETE RESTRICT;


--
-- Name: inventory fk_inventory_product; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.inventory
    ADD CONSTRAINT fk_inventory_product FOREIGN KEY (product_id) REFERENCES public.products(id) ON DELETE RESTRICT;


--
-- Name: movements fk_movement_from_location; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE wms.movements
    ADD CONSTRAINT fk_movement_from_location FOREIGN KEY (from_location_id) REFERENCES wms.locations(location_id) ON DELETE RESTRICT;


--
-- Name: movements fk_movement_product; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE wms.movements
    ADD CONSTRAINT fk_movement_product FOREIGN KEY (product_id) REFERENCES public.products(id) ON DELETE RESTRICT;


--
-- Name: movements fk_movement_to_location; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE wms.movements
    ADD CONSTRAINT fk_movement_to_location FOREIGN KEY (to_location_id) REFERENCES wms.locations(location_id) ON DELETE RESTRICT;


--
-- Name: containers fk_parent_container; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.containers
    ADD CONSTRAINT fk_parent_container FOREIGN KEY (parent_container_id) REFERENCES wms.containers(container_id) ON DELETE RESTRICT;


--
-- Name: locations fk_parent_location; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.locations
    ADD CONSTRAINT fk_parent_location FOREIGN KEY (parent_location_id) REFERENCES wms.locations(location_id) ON DELETE RESTRICT;


--
-- Name: receipt_items fk_receipt_product; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.receipt_items
    ADD CONSTRAINT fk_receipt_product FOREIGN KEY (product_id) REFERENCES public.products(id) ON DELETE RESTRICT;


--
-- Name: notifications notifications_related_task_id_fkey; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.notifications
    ADD CONSTRAINT notifications_related_task_id_fkey FOREIGN KEY (related_task_id) REFERENCES wms.tasks(task_id) ON DELETE CASCADE;


--
-- Name: notifications notifications_user_id_fkey; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.notifications
    ADD CONSTRAINT notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: task_items task_items_from_location_id_fkey; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.task_items
    ADD CONSTRAINT task_items_from_location_id_fkey FOREIGN KEY (from_location_id) REFERENCES wms.locations(location_id);


--
-- Name: task_items task_items_product_id_fkey; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.task_items
    ADD CONSTRAINT task_items_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.products(id);


--
-- Name: task_items task_items_task_id_fkey; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.task_items
    ADD CONSTRAINT task_items_task_id_fkey FOREIGN KEY (task_id) REFERENCES wms.tasks(task_id) ON DELETE CASCADE;


--
-- Name: tasks tasks_assigned_to_fkey; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.tasks
    ADD CONSTRAINT tasks_assigned_to_fkey FOREIGN KEY (assigned_to) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: tasks tasks_created_by_fkey; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.tasks
    ADD CONSTRAINT tasks_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: tasks tasks_from_location_id_fkey; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.tasks
    ADD CONSTRAINT tasks_from_location_id_fkey FOREIGN KEY (from_location_id) REFERENCES wms.locations(location_id);


--
-- Name: tasks tasks_parent_task_id_fkey; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.tasks
    ADD CONSTRAINT tasks_parent_task_id_fkey FOREIGN KEY (parent_task_id) REFERENCES wms.tasks(task_id);


--
-- Name: tasks tasks_to_location_id_fkey; Type: FK CONSTRAINT; Schema: wms; Owner: -
--

ALTER TABLE ONLY wms.tasks
    ADD CONSTRAINT tasks_to_location_id_fkey FOREIGN KEY (to_location_id) REFERENCES wms.locations(location_id);


--
-- PostgreSQL database dump complete
--

