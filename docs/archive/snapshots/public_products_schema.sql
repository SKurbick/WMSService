--
-- PostgreSQL database dump
--

-- STATUS: DB SNAPSHOT. This file records a point-in-time external schema and
-- does not guarantee the schema currently deployed in any environment.
--

\restrict ml1TokpHcNK9u4LrpoMPuasZIawiQU6Sp4Ts7x2oxDzbcdCS8IySzQv7yFmLlOk

-- Dumped from database version 17.4 (Debian 17.4-1.pgdg120+2)
-- Dumped by pg_dump version 18.1 (Ubuntu 18.1-1.pgdg24.04+2)

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

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: products; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.products (
    id character varying(50) NOT NULL,
    name character varying(255) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_kit boolean DEFAULT false,
    share_of_kit boolean DEFAULT false,
    kit_components jsonb,
    photo_link text,
    is_inventoried boolean,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    description text,
    category character varying(100),
    weight numeric(10,2),
    volume numeric(10,3),
    metadata jsonb,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: COLUMN products.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.products.description IS 'Описание товара. Используется для детальной информации о продукте.';


--
-- Name: COLUMN products.category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.products.category IS 'Категория товара. Пример: Электроника, Мебель, Одежда';


--
-- Name: COLUMN products.weight; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.products.weight IS 'Вес товара в килограммах. Используется для расчёта вместимости ячеек и оптимизации размещения. Пример: 2.50 (2.5 кг)';


--
-- Name: COLUMN products.volume; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.products.volume IS 'Объём товара в кубических метрах. Используется для расчёта вместимости ячеек. Пример: 0.025 (25 литров)';


--
-- Name: COLUMN products.metadata; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.products.metadata IS 'Дополнительные атрибуты товара в JSON формате. Пример: {"color": "silver", "warranty": "2 years", "brand": "Dell"}';


--
-- Name: COLUMN products.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.products.updated_at IS 'Дата и время последнего обновления записи.';


--
-- Name: products products_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_pkey PRIMARY KEY (id);


--
-- Name: idx_products_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_products_category ON public.products USING btree (category) WHERE (category IS NOT NULL);


--
-- Name: idx_products_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_products_is_active ON public.products USING btree (is_active) WHERE (is_active = true);


--
-- Name: products trg_after_insert_products; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_after_insert_products AFTER INSERT ON public.products FOR EACH ROW EXECUTE FUNCTION public.insert_default_current_balance();


--
-- Name: products trg_products_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_products_updated_at BEFORE UPDATE ON public.products FOR EACH ROW EXECUTE FUNCTION public.update_products_timestamp();


--
-- PostgreSQL database dump complete
--

\unrestrict ml1TokpHcNK9u4LrpoMPuasZIawiQU6Sp4Ts7x2oxDzbcdCS8IySzQv7yFmLlOk

current_state.md
api_map.md
database_map.md
database_indexes_constraints.md
business_rules.md
architecture_notes.md
decisions.md
invariants.md
known_issues.md
kit_operations.md