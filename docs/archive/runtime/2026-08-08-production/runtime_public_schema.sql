--
-- PostgreSQL database dump
--

\restrict Xx4BSfRS9Vb4bA7ntaArrMCcOBsTmHmNO72r8hRdILQ3JvxMoKpkCr4cYRi2Oxf

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
-- Name: assembly_task; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assembly_task (
    task_id bigint NOT NULL,
    article_id integer,
    vendor_code text NOT NULL,
    date date NOT NULL,
    wb_created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    supply_id text,
    order_uid text,
    rid text,
    scan_price numeric(20,2),
    color_code character varying(50),
    warehouse_id bigint,
    office_id bigint,
    chrt_id bigint,
    price numeric(20,2),
    converted_price numeric(20,2),
    currency_code smallint,
    converted_currency_code smallint,
    cargo_type smallint,
    full_address text,
    longitude numeric(9,6),
    latitude numeric(9,6),
    is_zero_address boolean DEFAULT false,
    is_shipped boolean DEFAULT false
);


--
-- Name: COLUMN assembly_task.scan_price; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.scan_price IS 'Цена сканирования (1500)';


--
-- Name: COLUMN assembly_task.color_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.color_code IS 'Код цвета (RAL 3017)';


--
-- Name: COLUMN assembly_task.warehouse_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.warehouse_id IS 'ID склада (658434)';


--
-- Name: COLUMN assembly_task.office_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.office_id IS 'ID офиса (123)';


--
-- Name: COLUMN assembly_task.chrt_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.chrt_id IS 'ID размера (987654321)';


--
-- Name: COLUMN assembly_task.price; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.price IS 'Цена (1014)';


--
-- Name: COLUMN assembly_task.converted_price; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.converted_price IS 'Конвертированная цена (28322)';


--
-- Name: COLUMN assembly_task.currency_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.currency_code IS 'Код валюты (933)';


--
-- Name: COLUMN assembly_task.converted_currency_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.converted_currency_code IS 'Код конвертированной валюты (643)';


--
-- Name: COLUMN assembly_task.cargo_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.cargo_type IS 'Тип груза (1)';


--
-- Name: COLUMN assembly_task.full_address; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.full_address IS 'Полный адрес';


--
-- Name: COLUMN assembly_task.longitude; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.longitude IS 'Долгота (44.519068)';


--
-- Name: COLUMN assembly_task.latitude; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assembly_task.latitude IS 'Широта (40.20192)';


--
-- Name: assembly_task_task_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.assembly_task_task_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: assembly_task_task_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.assembly_task_task_id_seq OWNED BY public.assembly_task.task_id;


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
-- Name: supply_to_sellers_warehouse; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supply_to_sellers_warehouse (
    id integer NOT NULL,
    guid character varying(45),
    document_number character varying(30),
    document_created_at timestamp without time zone,
    supply_date timestamp without time zone,
    local_vendor_code character varying(30),
    product_name character varying(300),
    event_status character varying(20),
    quantity numeric(15,2),
    amount_with_vat numeric(15,2),
    amount_without_vat numeric(15,2),
    supplier_name character varying(150),
    supplier_code character varying(30),
    update_document_datetime timestamp without time zone,
    author_of_the_change character varying(150),
    our_organizations_name character varying(250),
    is_valid boolean,
    planned_cost numeric(11,2),
    currency character varying(10),
    invoice_number character varying(30),
    transport_number character varying(35),
    pack_count numeric(11,2),
    pack_multiplicity numeric(11,2),
    order_guid character varying(45),
    truck_number character varying
);


--
-- Name: supply_to_sellers_warehouse_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.supply_to_sellers_warehouse_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: supply_to_sellers_warehouse_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.supply_to_sellers_warehouse_id_seq OWNED BY public.supply_to_sellers_warehouse.id;


--
-- Name: user_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_permissions (
    user_id integer,
    refresh_token text,
    edit_users boolean DEFAULT false NOT NULL,
    reception_of_goods boolean DEFAULT false NOT NULL,
    moving_goods_between_warehouses boolean DEFAULT false NOT NULL,
    movement_of_goods_between_warehouse_zones boolean DEFAULT false NOT NULL,
    creating_a_delivery boolean DEFAULT false NOT NULL,
    converting_sz_to_hanging boolean DEFAULT false NOT NULL,
    transfer_of_delivery_to_delivery boolean DEFAULT false NOT NULL,
    creation_of_a_reserve_fbo boolean DEFAULT false NOT NULL,
    sending_a_reserve_fbo boolean DEFAULT false NOT NULL,
    changing_product_characteristics boolean DEFAULT false NOT NULL,
    changing_the_product_name boolean DEFAULT false NOT NULL,
    adding_a_new_product boolean DEFAULT false NOT NULL,
    return_acceptance boolean DEFAULT false NOT NULL,
    ability_to_upload_excel_file_to_fines boolean DEFAULT false NOT NULL,
    viewing boolean DEFAULT true NOT NULL,
    download_excel_files boolean DEFAULT false NOT NULL,
    crm_viewing_settings boolean DEFAULT false NOT NULL,
    crm_viewing_warehouse boolean DEFAULT false NOT NULL,
    crm_viewing_task_of_store boolean DEFAULT false NOT NULL,
    crm_viewing_orders boolean DEFAULT false NOT NULL,
    crm_viewing_products boolean DEFAULT false NOT NULL,
    crm_viewing_promotions boolean DEFAULT false NOT NULL,
    crm_viewing_unit_economics boolean DEFAULT false NOT NULL,
    crm_viewing_crm_analytic boolean DEFAULT false NOT NULL,
    crm_change_price_and_discounts boolean DEFAULT false NOT NULL,
    crm_possibility_to_store_leftovers boolean DEFAULT false NOT NULL,
    crm_ability_to_add_and_remove_products_from_promotions boolean DEFAULT false NOT NULL,
    approve_discrepancies boolean DEFAULT false NOT NULL,
    procurement_role character varying(255) DEFAULT NULL::character varying,
    kiz_supplier boolean DEFAULT false NOT NULL,
    procurement_buyer boolean DEFAULT false NOT NULL,
    procurement_head_of_buyers boolean DEFAULT false NOT NULL
);


--
-- Name: COLUMN user_permissions.approve_discrepancies; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_permissions.approve_discrepancies IS 'Право подтверждать расхождения в заявках';


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying(50) NOT NULL,
    email character varying(100) NOT NULL,
    hashed_password character varying(255) NOT NULL,
    full_name character varying(100),
    disabled boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    is_superuser boolean DEFAULT false
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: assembly_task task_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assembly_task ALTER COLUMN task_id SET DEFAULT nextval('public.assembly_task_task_id_seq'::regclass);


--
-- Name: supply_to_sellers_warehouse id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supply_to_sellers_warehouse ALTER COLUMN id SET DEFAULT nextval('public.supply_to_sellers_warehouse_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: assembly_task assembly_task_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assembly_task
    ADD CONSTRAINT assembly_task_pkey PRIMARY KEY (task_id);


--
-- Name: products products_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_pkey PRIMARY KEY (id);


--
-- Name: supply_to_sellers_warehouse supply_to_sellers_warehouse_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supply_to_sellers_warehouse
    ADD CONSTRAINT supply_to_sellers_warehouse_pkey PRIMARY KEY (id);


--
-- Name: user_permissions user_permissions_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT user_permissions_user_id_key UNIQUE (user_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


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
-- Name: assembly_task assembly_task_article_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assembly_task
    ADD CONSTRAINT assembly_task_article_id_fkey FOREIGN KEY (article_id) REFERENCES public.article(nm_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: user_permissions user_permissions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT user_permissions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict Xx4BSfRS9Vb4bA7ntaArrMCcOBsTmHmNO72r8hRdILQ3JvxMoKpkCr4cYRi2Oxf

