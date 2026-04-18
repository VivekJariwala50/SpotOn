--
-- PostgreSQL database dump
--

\restrict 3TnqTXe0eOSqVrxhnVK8SsWK7cqr0Z9Jh426nexJrpZrNiVutgv5GArhetTO9J7

-- Dumped from database version 18.3
-- Dumped by pg_dump version 18.3

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
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- *not* creating schema, since initdb creates it


--
-- Name: btree_gist; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;


--
-- Name: EXTENSION btree_gist; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION btree_gist IS 'support for indexing common datatypes in GiST';


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: favorite_locations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.favorite_locations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    parking_lot_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: parking_lots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parking_lots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    address text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    price_per_hour numeric(10,2),
    parking_type text
);


--
-- Name: parking_slots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parking_slots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lot_id uuid NOT NULL,
    label text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    slot_type text DEFAULT 'standard'::text,
    supported_vehicle_type character varying(20),
    status text DEFAULT 'AVAILABLE'::text,
    CONSTRAINT parking_slots_supported_vehicle_type_check CHECK (((supported_vehicle_type)::text = ANY ((ARRAY['compact'::character varying, 'sedan'::character varying, 'suv'::character varying, 'truck'::character varying])::text[])))
);


--
-- Name: password_resets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_resets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pricing_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pricing_overrides (
    lot_id uuid NOT NULL,
    slot_type character varying(50) DEFAULT 'any'::character varying NOT NULL,
    vehicle_type character varying(50) DEFAULT 'any'::character varying NOT NULL,
    price_per_hour numeric(10,2) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pricing_overrides_price_per_hour_check CHECK ((price_per_hour >= (0)::numeric))
);


--
-- Name: profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    full_name text,
    phone text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: reservations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reservations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    slot_id uuid NOT NULL,
    start_time timestamp with time zone NOT NULL,
    end_time timestamp with time zone NOT NULL,
    status text DEFAULT 'CONFIRMED'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reservations_status_check CHECK ((status = ANY (ARRAY['CONFIRMED'::text, 'CANCELLED'::text, 'EXPIRED'::text]))),
    CONSTRAINT reservations_time_check CHECK ((end_time > start_time))
);


--
-- Name: transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transactions (
    id integer NOT NULL,
    reservation_id uuid,
    user_id uuid NOT NULL,
    transaction_type text NOT NULL,
    amount numeric(10,2) DEFAULT 0 NOT NULL,
    status text DEFAULT 'SUCCESS'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: transactions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.transactions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: transactions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.transactions_id_seq OWNED BY public.transactions.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    role text DEFAULT 'driver'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    full_name character varying(255),
    is_active boolean DEFAULT true NOT NULL,
    CONSTRAINT users_role_check CHECK ((role = ANY (ARRAY['driver'::text, 'operator'::text, 'admin'::text])))
);


--
-- Name: vehicles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vehicles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    plate_number text NOT NULL,
    vehicle_make text,
    vehicle_model text,
    vehicle_color text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    vehicle_type character varying(20),
    CONSTRAINT vehicles_vehicle_type_check CHECK (((vehicle_type)::text = ANY ((ARRAY['compact'::character varying, 'sedan'::character varying, 'suv'::character varying, 'truck'::character varying])::text[])))
);


--
-- Name: transactions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transactions ALTER COLUMN id SET DEFAULT nextval('public.transactions_id_seq'::regclass);


--
-- Data for Name: favorite_locations; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.favorite_locations (id, user_id, parking_lot_id, created_at) FROM stdin;
804f4853-09f6-4034-8e57-a11d0931a982	0b683cc7-1063-459a-9189-ecf4742f5cda	fba39614-c83f-4c8a-85b2-d38059e84f65	2026-03-23 21:56:07.734486-04
a9f73120-96a8-459a-afd4-d929e1fc64ef	0b683cc7-1063-459a-9189-ecf4742f5cda	efc132f8-2c7b-4c25-a3a8-420d6cf2005c	2026-03-23 21:57:35.695855-04
e89368ba-bf50-41e7-9e10-49b632160d1d	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	fba39614-c83f-4c8a-85b2-d38059e84f65	2026-04-14 10:19:08.484097-04
\.


--
-- Data for Name: parking_lots; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.parking_lots (id, name, address, created_at, price_per_hour, parking_type) FROM stdin;
d8512b78-f9eb-4455-968e-ef276c800606	Downtown Central Garage	123 Main St, Jersey City, NJ	2026-03-16 13:04:43.143632-04	8.00	Covered Parking
efc132f8-2c7b-4c25-a3a8-420d6cf2005c	Riverfront Parking Plaza	45 Hudson Ave, Hoboken, NJ	2026-03-16 13:04:43.143632-04	10.00	Multi-Level Garage
fba39614-c83f-4c8a-85b2-d38059e84f65	City Square Open Lot	78 Newark St, Hoboken, NJ	2026-03-16 13:04:43.143632-04	6.00	Open Parking Lot
3d43d3e1-70c2-41ee-a9b4-8feb00d8f4a1	Hoboken Waterfront Parking	12 Sinatra Dr, Hoboken, NJ	2026-03-16 18:32:19.722964-04	12.00	Covered Parking
9fe19b35-18cf-467a-b78f-8502fca15ca5	Journal Square EV Hub	50 Sip Ave, Jersey City, NJ	2026-03-16 18:32:19.722964-04	9.00	Open Parking Lot
b475f0db-9849-475f-8b09-4c85527acb90	Newport Covered Parking	125 River Dr S, Jersey City, NJ	2026-03-16 18:32:19.722964-04	11.00	Covered Parking
269858c5-ba7d-4c3f-b267-9ce006290c91	Exchange Place Smart Lot	1 Montgomery St, Jersey City, NJ	2026-03-16 18:32:19.722964-04	7.99	Multi-Level Garage
83c13b94-bd1f-4722-b2c0-542d6e47d89c	Midtown Secure Garage	200 W 34th St, New York, NY	2026-03-16 18:32:19.722964-04	5.00	Multi-Level Garage
\.


--
-- Data for Name: parking_slots; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.parking_slots (id, lot_id, label, is_active, created_at, slot_type, supported_vehicle_type, status) FROM stdin;
e9536f4b-7b71-4b85-a8c2-bec47b79d13d	d8512b78-f9eb-4455-968e-ef276c800606	A3	t	2026-03-16 13:04:43.149961-04	ev	suv	AVAILABLE
25f08adb-c85d-4383-b810-431599befa8a	83c13b94-bd1f-4722-b2c0-542d6e47d89c	M3	t	2026-03-16 18:32:19.729459-04	ev	suv	AVAILABLE
7d4ca35d-6175-474b-a0ab-675b5a58793d	3d43d3e1-70c2-41ee-a9b4-8feb00d8f4a1	H3	t	2026-03-16 18:32:19.730533-04	ev	suv	AVAILABLE
e1b44413-78ca-4230-b770-4a74cd74f4ee	9fe19b35-18cf-467a-b78f-8502fca15ca5	J3	t	2026-03-16 18:32:19.731579-04	standard	suv	AVAILABLE
9c9c516e-4a5d-48ec-9e80-72f39999c928	b475f0db-9849-475f-8b09-4c85527acb90	N3	t	2026-03-16 18:32:19.732581-04	accessible	suv	AVAILABLE
aea3048b-3f70-4596-944e-b752d9d1492d	269858c5-ba7d-4c3f-b267-9ce006290c91	E3	t	2026-03-16 18:32:19.733593-04	ev	suv	AVAILABLE
37660e47-a7f5-4a42-ac0e-d9003e2ff8a2	83c13b94-bd1f-4722-b2c0-542d6e47d89c	M4	t	2026-03-16 18:32:19.729643-04	accessible	truck	AVAILABLE
b2ed9466-008f-459d-a20a-7589759c7ff9	3d43d3e1-70c2-41ee-a9b4-8feb00d8f4a1	H4	t	2026-03-16 18:32:19.730779-04	accessible	truck	AVAILABLE
d3f302ea-288f-4765-bf2e-8e84df0eee5a	9fe19b35-18cf-467a-b78f-8502fca15ca5	J4	t	2026-03-16 18:32:19.73178-04	accessible	truck	AVAILABLE
2411e68d-6746-4221-b143-8165e910a669	b475f0db-9849-475f-8b09-4c85527acb90	N4	t	2026-03-16 18:32:19.732779-04	ev	truck	AVAILABLE
543424bd-70c5-47a5-803c-263f42f88478	269858c5-ba7d-4c3f-b267-9ce006290c91	E4	t	2026-03-16 18:32:19.733798-04	accessible	truck	AVAILABLE
a6c85319-7707-4f85-a5c3-5d42ce2b22f0	3d43d3e1-70c2-41ee-a9b4-8feb00d8f4a1	H5	t	2026-03-16 18:32:19.730963-04	standard	sedan	AVAILABLE
53b3f605-47f6-4606-afec-eab5ed8a4c17	9fe19b35-18cf-467a-b78f-8502fca15ca5	J5	f	2026-03-16 18:32:19.731978-04	standard	sedan	AVAILABLE
6628e2e0-fcce-46b5-bc1b-7f7c98d9764e	b475f0db-9849-475f-8b09-4c85527acb90	N5	t	2026-03-16 18:32:19.732986-04	standard	sedan	AVAILABLE
623e377b-5c20-42d6-80b4-6c5023514493	269858c5-ba7d-4c3f-b267-9ce006290c91	E5	t	2026-03-16 18:32:19.734005-04	standard	sedan	AVAILABLE
b5b7df4b-50f7-4408-9cd3-195ecf234bd8	fba39614-c83f-4c8a-85b2-d38059e84f65	V8	f	2026-04-14 09:33:51.861561-04	standard	compact	AVAILABLE
efb65f28-cf2f-45d1-9b9f-c7f5aa164501	9fe19b35-18cf-467a-b78f-8502fca15ca5	J1	f	2026-03-16 18:32:19.731168-04	ev	compact	AVAILABLE
aa453c73-0565-4f00-8324-05d50a548fd1	269858c5-ba7d-4c3f-b267-9ce006290c91	E1	f	2026-03-16 18:32:19.733186-04	standard	compact	AVAILABLE
32c9e26b-b739-4df7-b378-3d60cb4bad46	d8512b78-f9eb-4455-968e-ef276c800606	A1	t	2026-03-16 13:04:43.148033-04	standard	compact	AVAILABLE
21239501-224e-4d4e-970f-7281b3b30dee	efc132f8-2c7b-4c25-a3a8-420d6cf2005c	B1	t	2026-03-16 13:04:43.150209-04	standard	compact	AVAILABLE
af2fa4f4-cffd-4f6e-a5b9-6b74112f2de2	fba39614-c83f-4c8a-85b2-d38059e84f65	C1	t	2026-03-16 13:04:43.150634-04	standard	compact	AVAILABLE
126c2cc3-6e9f-47eb-8f92-714288ddd05a	83c13b94-bd1f-4722-b2c0-542d6e47d89c	M1	t	2026-03-16 18:32:19.728054-04	standard	compact	AVAILABLE
8819fc9f-5978-4ea5-9bb6-95f80c2ebdd9	3d43d3e1-70c2-41ee-a9b4-8feb00d8f4a1	H1	t	2026-03-16 18:32:19.730112-04	standard	compact	AVAILABLE
cafd7fc1-9c60-4a6b-aa44-3517b7e0a866	b475f0db-9849-475f-8b09-4c85527acb90	N1	t	2026-03-16 18:32:19.732174-04	standard	compact	AVAILABLE
ec90ce03-825e-4bae-9610-0d01a1a82ff0	d8512b78-f9eb-4455-968e-ef276c800606	A2	t	2026-03-16 13:04:43.149618-04	standard	sedan	AVAILABLE
796464af-464b-4ede-8ad4-e53eae6a5cb5	efc132f8-2c7b-4c25-a3a8-420d6cf2005c	B2	t	2026-03-16 13:04:43.150421-04	accessible	sedan	AVAILABLE
4d9b0151-0cce-4355-be76-67f782614229	83c13b94-bd1f-4722-b2c0-542d6e47d89c	M2	t	2026-03-16 18:32:19.729287-04	standard	sedan	AVAILABLE
369736bd-5c58-4dd0-b914-6ea94a4d9888	3d43d3e1-70c2-41ee-a9b4-8feb00d8f4a1	H2	t	2026-03-16 18:32:19.730337-04	standard	sedan	AVAILABLE
c021cd0e-df5e-4be1-95fa-af3a7e248282	9fe19b35-18cf-467a-b78f-8502fca15ca5	J2	t	2026-03-16 18:32:19.731377-04	ev	sedan	AVAILABLE
c6608ad3-c2b4-4ee5-8dca-cd12bc858dcb	b475f0db-9849-475f-8b09-4c85527acb90	N2	t	2026-03-16 18:32:19.732381-04	standard	sedan	AVAILABLE
079c361c-9441-4a11-ba23-e12918022e6c	269858c5-ba7d-4c3f-b267-9ce006290c91	E2	t	2026-03-16 18:32:19.733383-04	standard	sedan	AVAILABLE
71693861-64d6-4318-938d-8c7fd3dc7e80	83c13b94-bd1f-4722-b2c0-542d6e47d89c	M6	t	2026-04-14 21:28:17.949201-04	accessible	suv	AVAILABLE
8f5d869d-552e-4416-a18c-a395b08dc414	83c13b94-bd1f-4722-b2c0-542d6e47d89c	M5	f	2026-03-16 18:32:19.729873-04	standard	sedan	OUT_OF_SERVICE
\.


--
-- Data for Name: password_resets; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.password_resets (id, user_id, token, expires_at, used, created_at) FROM stdin;
9298302e-b614-481b-91a2-ac57ce9169dd	cd444d86-d309-456d-aacc-7e7c3180b8b7	zkUpqh8Oud2bdOd7lIpU16THwIe7tjmFhRc28R9xn-4	2026-03-16 23:31:33.494304-04	t	2026-03-16 22:31:33.494304-04
f36b640c-9a6c-4b09-87f8-acf245d31500	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	PmggO--anMGFpoQLJhm_IY_tKGD7PB06U2u9uhvzn4U	2026-03-17 23:45:22.214047-04	t	2026-03-17 22:45:22.214047-04
2df15833-892e-409b-9be5-f2ea9e1e7876	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	x5RKI6WgqEdXwIU_6lSDwLo6ETjvCbNIdTZhV_PbOUs	2026-03-23 17:36:35.980372-04	t	2026-03-23 16:36:35.980372-04
\.


--
-- Data for Name: pricing_overrides; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.pricing_overrides (lot_id, slot_type, vehicle_type, price_per_hour, updated_at) FROM stdin;
d8512b78-f9eb-4455-968e-ef276c800606	any	any	9.00	2026-04-14 12:50:01.10134-04
\.


--
-- Data for Name: profiles; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.profiles (id, user_id, full_name, phone, created_at, updated_at) FROM stdin;
033d4c7d-a26b-469a-9521-519bc9ab4bd6	cd444d86-d309-456d-aacc-7e7c3180b8b7	Daanish Shaikh	8624408424	2026-03-16 18:59:01.940048-04	2026-03-16 18:59:01.940048-04
2392b48e-6e50-416a-a8fc-83a579e5d160	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	Demo Driver	854745521	2026-03-17 22:42:56.742887-04	2026-03-23 22:28:53.206783-04
\.


--
-- Data for Name: reservations; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.reservations (id, user_id, slot_id, start_time, end_time, status, created_at) FROM stdin;
28517b13-7d0f-477a-b1ad-e72e724043ae	f5d5dedd-d4a5-4ace-80fa-a56e8c84b306	126c2cc3-6e9f-47eb-8f92-714288ddd05a	2026-03-16 19:32:19.734222-04	2026-03-16 20:32:19.734222-04	CONFIRMED	2026-03-16 18:32:19.734222-04
e1571ad9-6b65-41ad-92cc-4f1e34041e80	f5d5dedd-d4a5-4ace-80fa-a56e8c84b306	7d4ca35d-6175-474b-a0ab-675b5a58793d	2026-03-16 19:02:19.738851-04	2026-03-16 20:02:19.738851-04	CONFIRMED	2026-03-16 18:32:19.738851-04
e1581117-6ea5-4c72-b185-33087fc3a0af	f5d5dedd-d4a5-4ace-80fa-a56e8c84b306	c021cd0e-df5e-4be1-95fa-af3a7e248282	2026-03-16 20:32:19.739235-04	2026-03-16 22:32:19.739235-04	CONFIRMED	2026-03-16 18:32:19.739235-04
feaac07a-db7c-4d13-b508-98351d994de8	f5d5dedd-d4a5-4ace-80fa-a56e8c84b306	2411e68d-6746-4221-b143-8165e910a669	2026-03-17 18:32:19.739555-04	2026-03-17 20:32:19.739555-04	CONFIRMED	2026-03-16 18:32:19.739555-04
384a6b0e-d6d1-492c-8fcc-481f90ef7b9c	f5d5dedd-d4a5-4ace-80fa-a56e8c84b306	aea3048b-3f70-4596-944e-b752d9d1492d	2026-03-16 21:32:19.739875-04	2026-03-16 23:32:19.739875-04	CONFIRMED	2026-03-16 18:32:19.739875-04
74ddfac3-2b82-4dad-a38f-64c234b25fef	cd444d86-d309-456d-aacc-7e7c3180b8b7	126c2cc3-6e9f-47eb-8f92-714288ddd05a	2026-03-16 20:36:00-04	2026-03-16 21:36:00-04	CONFIRMED	2026-03-16 18:36:50.481612-04
f528e849-ae0d-4e5e-a02b-250d5ac87283	cd444d86-d309-456d-aacc-7e7c3180b8b7	37660e47-a7f5-4a42-ac0e-d9003e2ff8a2	2026-03-16 18:48:00-04	2026-03-16 19:48:00-04	CONFIRMED	2026-03-16 18:47:50.150251-04
bb4eee4c-0c32-4923-97c1-30f3dc93cb60	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	aa453c73-0565-4f00-8324-05d50a548fd1	2026-03-17 22:42:00-04	2026-03-17 23:42:00-04	CANCELLED	2026-03-17 22:41:34.785627-04
9b68b62c-b6cf-4c6a-8061-c12a7a09d3a0	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	e9536f4b-7b71-4b85-a8c2-bec47b79d13d	2026-03-23 19:30:00-04	2026-03-23 20:30:00-04	CANCELLED	2026-03-23 19:03:50.175808-04
cd6386b1-4305-4770-8469-f3734a603e53	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-03-23 19:30:00-04	2026-03-23 20:30:00-04	CANCELLED	2026-03-23 19:03:22.494568-04
18f22a89-cd70-4ffe-ad24-a1aef72e4ce2	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	ec90ce03-825e-4bae-9610-0d01a1a82ff0	2026-03-23 19:30:00-04	2026-03-23 20:00:00-04	CANCELLED	2026-03-23 19:06:33.118724-04
9ec3c7f7-9ba6-4d3e-9344-1e968f698045	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-03-23 19:30:00-04	2026-03-23 20:00:00-04	CANCELLED	2026-03-23 19:18:41.258895-04
57743067-0365-4bfe-83b9-fa073d2077ba	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	cafd7fc1-9c60-4a6b-aa44-3517b7e0a866	2026-03-23 19:30:00-04	2026-03-23 20:00:00-04	CONFIRMED	2026-03-23 19:23:53.607359-04
af442323-d85b-4790-bd13-42f1dc781baf	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-03-23 22:00:00-04	2026-03-23 22:30:00-04	CONFIRMED	2026-03-23 21:47:09.678757-04
23add6c7-72e5-42d1-9e1b-9464a2ef4020	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-03-23 23:00:00-04	2026-03-23 23:30:00-04	CONFIRMED	2026-03-23 22:47:05.419991-04
9086a41d-6836-4291-9dee-7a8364c8ec94	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-03-24 11:00:00-04	2026-03-24 11:30:00-04	CANCELLED	2026-03-24 10:55:08.997839-04
d485f998-67cf-4379-9d37-ee7a9b97a3ca	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-03-24 11:00:00-04	2026-03-24 11:30:00-04	CONFIRMED	2026-03-24 10:56:10.679159-04
ef9c03c7-4f4d-4bae-a9e1-884e656e35d8	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	ec90ce03-825e-4bae-9610-0d01a1a82ff0	2026-03-24 11:00:00-04	2026-03-24 11:30:00-04	CANCELLED	2026-03-24 10:59:40.677189-04
6c393d16-500d-445f-abd8-852747afc6b7	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	af2fa4f4-cffd-4f6e-a5b9-6b74112f2de2	2026-03-27 16:30:00-04	2026-03-27 18:00:00-04	CONFIRMED	2026-03-27 14:21:22.713613-04
af836f98-24a1-4562-814b-30ce29d97dac	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-03-27 15:00:00-04	2026-03-27 15:30:00-04	CONFIRMED	2026-03-27 14:40:19.254332-04
3749ba34-aa62-478b-9210-8e3e6df10828	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	126c2cc3-6e9f-47eb-8f92-714288ddd05a	2026-03-28 09:00:00-04	2026-03-28 11:00:00-04	CONFIRMED	2026-03-27 15:02:57.500911-04
f8ee5b41-5d44-412a-9ee3-93ede830653c	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	126c2cc3-6e9f-47eb-8f92-714288ddd05a	2026-03-27 16:00:00-04	2026-03-27 17:00:00-04	CONFIRMED	2026-03-27 15:40:33.022459-04
8593791f-8c5e-448a-b595-36747589a4dc	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	4d9b0151-0cce-4355-be76-67f782614229	2026-03-27 16:30:00-04	2026-03-27 17:00:00-04	CONFIRMED	2026-03-27 16:03:42.985624-04
c78f2a4f-be1c-4c07-8640-360150c4c55e	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	c6608ad3-c2b4-4ee5-8dca-cd12bc858dcb	2026-03-30 22:30:00-04	2026-03-31 02:30:00-04	CANCELLED	2026-03-30 21:43:50.16944-04
0c5436e3-90c5-43f2-aae0-b44a2e25d693	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	cafd7fc1-9c60-4a6b-aa44-3517b7e0a866	2026-03-30 23:00:00-04	2026-03-31 01:00:00-04	CONFIRMED	2026-03-30 22:15:28.434171-04
8f4d7dcf-f38f-45b4-af3e-0c0587bf7f01	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	af2fa4f4-cffd-4f6e-a5b9-6b74112f2de2	2026-03-31 10:00:00-04	2026-03-31 11:30:00-04	CANCELLED	2026-03-31 09:24:17.826269-04
eb188b4b-0f51-4e89-a78a-d96ee88a22bd	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	369736bd-5c58-4dd0-b914-6ea94a4d9888	2026-03-31 10:00:00-04	2026-03-31 10:30:00-04	CONFIRMED	2026-03-31 09:39:14.357981-04
fbd47d73-34bb-475d-ab9a-0bad87078ed2	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-04-01 09:00:00-04	2026-04-01 10:00:00-04	CANCELLED	2026-03-31 10:21:05.540601-04
a6a6843f-7f9b-4dd3-8b59-a3e06b6a2994	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	af2fa4f4-cffd-4f6e-a5b9-6b74112f2de2	2026-03-31 17:00:00-04	2026-03-31 17:30:00-04	CANCELLED	2026-03-31 10:32:10.42408-04
5a32aa89-c457-432d-9277-bc229705f21a	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-03-31 11:00:00-04	2026-03-31 11:30:00-04	CONFIRMED	2026-03-31 10:57:28.010682-04
60942649-8137-46ac-930d-b7b0bbd17604	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-04-14 21:00:00-04	2026-04-14 22:00:00-04	CANCELLED	2026-04-14 20:50:25.033942-04
077f38fa-a450-4b3c-9702-08182916e4af	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-04-15 09:00:00-04	2026-04-15 13:00:00-04	CANCELLED	2026-04-14 21:23:32.498776-04
c163604f-5f4b-4b85-8fab-7bda3ef91f32	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-04-15 12:00:00-04	2026-04-15 12:30:00-04	CONFIRMED	2026-04-15 11:55:19.323219-04
d854520b-b771-4edf-8cf4-19fbea4affe0	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	32c9e26b-b739-4df7-b378-3d60cb4bad46	2026-04-15 18:30:00-04	2026-04-15 19:00:00-04	CONFIRMED	2026-04-15 17:55:12.699084-04
\.


--
-- Data for Name: transactions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.transactions (id, reservation_id, user_id, transaction_type, amount, status, created_at) FROM stdin;
23	c163604f-5f4b-4b85-8fab-7bda3ef91f32	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	CREATE_RESERVATION	3.38	SUCCESS	2026-04-15 11:55:19.323219-04
24	d854520b-b771-4edf-8cf4-19fbea4affe0	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	CREATE_RESERVATION	4.50	SUCCESS	2026-04-15 17:55:12.699084-04
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.users (id, email, password_hash, role, created_at, full_name, is_active) FROM stdin;
f5d5dedd-d4a5-4ace-80fa-a56e8c84b306	test@example.com	x	driver	2026-02-15 14:57:09.841232-05	\N	t
dc55e18c-7be9-4f47-8733-b1a9cfa334bd	demodriver@spoton.com	scrypt:32768:8:1$cJpfSs0PnGruLDyP$8998dbbdb77abafa325ed8470ad386266916466f76dc8ca210541aa01a34d7e0d25e81b36ed8c574f9e668d97d59fc46b8221b990933b00ed9751e241bfac0c7	driver	2026-03-17 22:38:31.593432-04	\N	t
cd444d86-d309-456d-aacc-7e7c3180b8b7	daanishdriver@spoton.com	scrypt:32768:8:1$uUl2XWgRSNoMb4FJ$2e8b547bf474c3ea5e35a0b246bf08930deb69c6628a7864511eb8d5b48a524c825ae18f8277650316c9d633941fc8a000219d68961b9c8d6b2d186deb320aea	driver	2026-03-16 13:18:44.164718-04	\N	f
d25038e3-b154-4d25-b3ff-8b5765b06ea7	vyomadmin@spoton.com	scrypt:32768:8:1$CStHnfeHVUSAvc8K$62754a2d21641bf1fdc2e2b716dd04d3a77ed695e8eeb45e14e3180e20dce4dfb09778f51ba795de0be831d80a2428b5521a689812d4cb852eaf2193a8b0346a	admin	2026-03-16 13:19:35.190417-04	\N	t
c15665f5-f285-4709-b7e7-22390b50c20a	vyomoperator@spoton.com	scrypt:32768:8:1$GTizi5jt8SHXNvLl$eb71f6158b5dfd2f07f7ece1afc31f35563288077c7f95db00c3fddf866a3c5b9bf9df930aa3e09754306275d5bffb6103152e8d1cc9fbd9fef1b4e7e6ccbd78	operator	2026-02-15 14:57:58.746033-05	\N	t
0b683cc7-1063-459a-9189-ecf4742f5cda	hassandriver@spoton.com	scrypt:32768:8:1$NaRhEiuHzCWVqbvu$f7c053405fe26a66ed3c63da3ff2af600d6d83337944374271254cc62f36e3ebf1f45c9f5dc0ebc0a559815f284efb18079168c26298898b23db6586b25eb5f1	driver	2026-03-23 21:54:16.152449-04	Md Hassan Abi Ali Khan	t
79bf2cc3-f0a8-4b14-bb40-8813360caeff	daanishadmin@spoton.com	scrypt:32768:8:1$6KEVXFZ6VqIYhqsZ$c01a927bfdd0dab5072d76ddbf705fd4b8aa8d678eb26c461847d0467e254a38a6592b1260845cdf0187ea82b7dd49f8c80fa97210a1214133eb9aed95b3f10c	admin	2026-03-24 10:37:13.538169-04	Daanish Admin	t
2bd59486-dd31-431f-acd0-babbceaefe8f	demooperator@spoton.com	scrypt:32768:8:1$8HkbF0JBTezAPI9G$9e3899cf75a62036f42e42579176ce586bcaea99b40d8dc6c98f3a5717c5e6268e1778f595d2961e0132575a9f44e9c11c74e545abbb3169df5825d780a0f9f0	operator	2026-03-24 10:49:37.473068-04	DemoOp	t
08a49186-925a-496a-af7c-b24d5024872f	demodriver2@spoton.com	scrypt:32768:8:1$Z3kLbsDqjEEGH01p$280cad9abf25728d87583eb7135300780b0f43eb2f7bbb59861dc00558ca23bcb4be1395a9178b2f4543d8c75b5ed693802637cdcd0acc1557d24bbd4b12b74f	driver	2026-03-24 10:49:00.397071-04	DemoDriver2	f
\.


--
-- Data for Name: vehicles; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.vehicles (id, user_id, plate_number, vehicle_make, vehicle_model, vehicle_color, created_at, vehicle_type) FROM stdin;
93c81f8e-89e4-45fa-89b8-58e4f2f84d0a	cd444d86-d309-456d-aacc-7e7c3180b8b7	DAN0416	BMW	M5	Red	2026-03-16 19:00:15.300754-04	\N
b3196ca0-c94a-429b-abb4-64ddab332ad2	cd444d86-d309-456d-aacc-7e7c3180b8b7	SHIV332	Porshe	911 Turbo	Pink	2026-03-16 21:40:03.04546-04	\N
c34f9302-933b-4936-a214-cc800a77c1ac	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	DEM789	GMC	Yukon	Black	2026-03-23 17:03:21.327991-04	suv
a7c23a2a-16b7-494d-a789-dd054d70f357	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	DEM142	Mini	Cooper	Yellow	2026-03-23 17:04:09.133019-04	compact
7244a36f-37de-4993-bead-dcf9295c2605	0b683cc7-1063-459a-9189-ecf4742f5cda	KHAN786	Toyota	Fortuner	White	2026-03-23 21:55:16.880549-04	suv
bdf24eda-1a3c-443a-8beb-18bd9c83a2df	0b683cc7-1063-459a-9189-ecf4742f5cda	KHAN001	Skoda	Slavia	Silver	2026-03-23 21:55:39.993238-04	sedan
4e5348b3-9d10-435a-966f-a276d967e217	dc55e18c-7be9-4f47-8733-b1a9cfa334bd	DEMO008	BMW	M3	Green	2026-03-31 09:38:54.247654-04	sedan
\.


--
-- Name: transactions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.transactions_id_seq', 24, true);


--
-- Name: favorite_locations favorite_locations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.favorite_locations
    ADD CONSTRAINT favorite_locations_pkey PRIMARY KEY (id);


--
-- Name: favorite_locations favorite_locations_user_id_parking_lot_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.favorite_locations
    ADD CONSTRAINT favorite_locations_user_id_parking_lot_id_key UNIQUE (user_id, parking_lot_id);


--
-- Name: parking_lots parking_lots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parking_lots
    ADD CONSTRAINT parking_lots_pkey PRIMARY KEY (id);


--
-- Name: parking_slots parking_slots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parking_slots
    ADD CONSTRAINT parking_slots_pkey PRIMARY KEY (id);


--
-- Name: password_resets password_resets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_pkey PRIMARY KEY (id);


--
-- Name: password_resets password_resets_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_token_key UNIQUE (token);


--
-- Name: pricing_overrides pricing_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pricing_overrides
    ADD CONSTRAINT pricing_overrides_pkey PRIMARY KEY (lot_id, slot_type, vehicle_type);


--
-- Name: profiles profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_pkey PRIMARY KEY (id);


--
-- Name: profiles profiles_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_user_id_key UNIQUE (user_id);


--
-- Name: reservations reservations_confirmed_no_overlap; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_confirmed_no_overlap EXCLUDE USING gist (slot_id WITH =, tstzrange(start_time, end_time, '[)'::text) WITH &&) WHERE ((status = 'CONFIRMED'::text));


--
-- Name: reservations reservations_no_overlap_confirmed; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_no_overlap_confirmed EXCLUDE USING gist (slot_id WITH =, tstzrange(start_time, end_time, '[)'::text) WITH &&) WHERE ((status = 'CONFIRMED'::text));


--
-- Name: reservations reservations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_pkey PRIMARY KEY (id);


--
-- Name: transactions transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_pkey PRIMARY KEY (id);


--
-- Name: parking_slots unique_slot_label_per_lot; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parking_slots
    ADD CONSTRAINT unique_slot_label_per_lot UNIQUE (lot_id, label);


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
-- Name: vehicles vehicles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_pkey PRIMARY KEY (id);


--
-- Name: vehicles vehicles_user_id_plate_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_user_id_plate_number_key UNIQUE (user_id, plate_number);


--
-- Name: idx_parking_slots_lot_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parking_slots_lot_id ON public.parking_slots USING btree (lot_id);


--
-- Name: idx_reservations_slot_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reservations_slot_id ON public.reservations USING btree (slot_id);


--
-- Name: idx_reservations_slot_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reservations_slot_time ON public.reservations USING btree (slot_id, start_time, end_time);


--
-- Name: idx_reservations_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reservations_user_id ON public.reservations USING btree (user_id);


--
-- Name: parking_slots_lot_id_label_normalized_uidx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX parking_slots_lot_id_label_normalized_uidx ON public.parking_slots USING btree (lot_id, upper(btrim(label)));


--
-- Name: favorite_locations favorite_locations_parking_lot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.favorite_locations
    ADD CONSTRAINT favorite_locations_parking_lot_id_fkey FOREIGN KEY (parking_lot_id) REFERENCES public.parking_lots(id) ON DELETE CASCADE;


--
-- Name: favorite_locations favorite_locations_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.favorite_locations
    ADD CONSTRAINT favorite_locations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: parking_slots parking_slots_lot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parking_slots
    ADD CONSTRAINT parking_slots_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES public.parking_lots(id) ON DELETE CASCADE;


--
-- Name: password_resets password_resets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: pricing_overrides pricing_overrides_lot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pricing_overrides
    ADD CONSTRAINT pricing_overrides_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES public.parking_lots(id) ON DELETE CASCADE;


--
-- Name: profiles profiles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: reservations reservations_slot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_slot_id_fkey FOREIGN KEY (slot_id) REFERENCES public.parking_slots(id) ON DELETE CASCADE;


--
-- Name: reservations reservations_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: transactions transactions_reservation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_reservation_id_fkey FOREIGN KEY (reservation_id) REFERENCES public.reservations(id) ON DELETE SET NULL;


--
-- Name: transactions transactions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: vehicles vehicles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict 3TnqTXe0eOSqVrxhnVK8SsWK7cqr0Z9Jh426nexJrpZrNiVutgv5GArhetTO9J7

