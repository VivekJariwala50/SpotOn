--
-- PostgreSQL database dump
--

\restrict g3vMCobYTx6Vf9nLiBI6CV3C5ewbngfYswqDT7XbVkLJvFOgsIAf1dDjM4BVIa5

-- Dumped from database version 18.3
-- Dumped by pg_dump version 18.2

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
-- Name: public; Type: SCHEMA; Schema: -; Owner: vyomraj
--

-- *not* creating schema, since initdb creates it


ALTER SCHEMA public OWNER TO vyomraj;

--
-- Name: btree_gist; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;


--
-- Name: EXTENSION btree_gist; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION btree_gist IS 'support for indexing common datatypes in GiST';


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: favorite_locations; Type: TABLE; Schema: public; Owner: vyomraj
--

CREATE TABLE public.favorite_locations (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    parking_lot_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.favorite_locations OWNER TO vyomraj;

--
-- Name: parking_lots; Type: TABLE; Schema: public; Owner: vyomraj
--

CREATE TABLE public.parking_lots (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name text NOT NULL,
    address text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    price_per_hour numeric(10,2),
    parking_type text
);


ALTER TABLE public.parking_lots OWNER TO vyomraj;

--
-- Name: parking_slots; Type: TABLE; Schema: public; Owner: vyomraj
--

CREATE TABLE public.parking_slots (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    lot_id uuid NOT NULL,
    label text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    slot_type text DEFAULT 'standard'::text
);


ALTER TABLE public.parking_slots OWNER TO vyomraj;

--
-- Name: password_resets; Type: TABLE; Schema: public; Owner: vyomraj
--

CREATE TABLE public.password_resets (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    token text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.password_resets OWNER TO vyomraj;

--
-- Name: profiles; Type: TABLE; Schema: public; Owner: vyomraj
--

CREATE TABLE public.profiles (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    full_name text,
    phone text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.profiles OWNER TO vyomraj;

--
-- Name: reservations; Type: TABLE; Schema: public; Owner: vyomraj
--

CREATE TABLE public.reservations (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    slot_id uuid NOT NULL,
    start_time timestamp with time zone NOT NULL,
    end_time timestamp with time zone NOT NULL,
    status text DEFAULT 'CONFIRMED'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reservations_status_check CHECK ((status = ANY (ARRAY['CONFIRMED'::text, 'CANCELLED'::text, 'EXPIRED'::text]))),
    CONSTRAINT reservations_time_check CHECK ((end_time > start_time))
);


ALTER TABLE public.reservations OWNER TO vyomraj;

--
-- Name: users; Type: TABLE; Schema: public; Owner: vyomraj
--

CREATE TABLE public.users (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    role text DEFAULT 'user'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT users_role_check CHECK ((role = ANY (ARRAY['user'::text, 'admin'::text])))
);


ALTER TABLE public.users OWNER TO vyomraj;

--
-- Name: vehicles; Type: TABLE; Schema: public; Owner: vyomraj
--

CREATE TABLE public.vehicles (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    plate_number text NOT NULL,
    vehicle_make text,
    vehicle_model text,
    vehicle_color text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.vehicles OWNER TO vyomraj;

--
-- Data for Name: favorite_locations; Type: TABLE DATA; Schema: public; Owner: vyomraj
--

COPY public.favorite_locations (id, user_id, parking_lot_id, created_at) FROM stdin;
\.


--
-- Data for Name: parking_lots; Type: TABLE DATA; Schema: public; Owner: vyomraj
--

COPY public.parking_lots (id, name, address, created_at, price_per_hour, parking_type) FROM stdin;
d8512b78-f9eb-4455-968e-ef276c800606	Downtown Central Garage	123 Main St, Jersey City, NJ	2026-03-16 13:04:43.143632-04	8.00	Covered Parking
efc132f8-2c7b-4c25-a3a8-420d6cf2005c	Riverfront Parking Plaza	45 Hudson Ave, Hoboken, NJ	2026-03-16 13:04:43.143632-04	10.00	Multi-Level Garage
fba39614-c83f-4c8a-85b2-d38059e84f65	City Square Open Lot	78 Newark St, Hoboken, NJ	2026-03-16 13:04:43.143632-04	6.00	Open Parking Lot
83c13b94-bd1f-4722-b2c0-542d6e47d89c	Midtown Secure Garage	200 W 34th St, New York, NY	2026-03-16 18:32:19.722964-04	15.00	Multi-Level Garage
3d43d3e1-70c2-41ee-a9b4-8feb00d8f4a1	Hoboken Waterfront Parking	12 Sinatra Dr, Hoboken, NJ	2026-03-16 18:32:19.722964-04	12.00	Covered Parking
9fe19b35-18cf-467a-b78f-8502fca15ca5	Journal Square EV Hub	50 Sip Ave, Jersey City, NJ	2026-03-16 18:32:19.722964-04	9.00	Open Parking Lot
b475f0db-9849-475f-8b09-4c85527acb90	Newport Covered Parking	125 River Dr S, Jersey City, NJ	2026-03-16 18:32:19.722964-04	11.00	Covered Parking
269858c5-ba7d-4c3f-b267-9ce006290c91	Exchange Place Smart Lot	1 Montgomery St, Jersey City, NJ	2026-03-16 18:32:19.722964-04	13.00	Multi-Level Garage
\.


--
-- Data for Name: parking_slots; Type: TABLE DATA; Schema: public; Owner: vyomraj
--

COPY public.parking_slots (id, lot_id, label, is_active, created_at, slot_type) FROM stdin;
32c9e26b-b739-4df7-b378-3d60cb4bad46	d8512b78-f9eb-4455-968e-ef276c800606	A1	t	2026-03-16 13:04:43.148033-04	standard
ec90ce03-825e-4bae-9610-0d01a1a82ff0	d8512b78-f9eb-4455-968e-ef276c800606	A2	t	2026-03-16 13:04:43.149618-04	standard
21239501-224e-4d4e-970f-7281b3b30dee	efc132f8-2c7b-4c25-a3a8-420d6cf2005c	B1	t	2026-03-16 13:04:43.150209-04	standard
af2fa4f4-cffd-4f6e-a5b9-6b74112f2de2	fba39614-c83f-4c8a-85b2-d38059e84f65	C1	t	2026-03-16 13:04:43.150634-04	standard
e9536f4b-7b71-4b85-a8c2-bec47b79d13d	d8512b78-f9eb-4455-968e-ef276c800606	A3	t	2026-03-16 13:04:43.149961-04	ev
796464af-464b-4ede-8ad4-e53eae6a5cb5	efc132f8-2c7b-4c25-a3a8-420d6cf2005c	B2	t	2026-03-16 13:04:43.150421-04	accessible
126c2cc3-6e9f-47eb-8f92-714288ddd05a	83c13b94-bd1f-4722-b2c0-542d6e47d89c	M1	t	2026-03-16 18:32:19.728054-04	standard
4d9b0151-0cce-4355-be76-67f782614229	83c13b94-bd1f-4722-b2c0-542d6e47d89c	M2	t	2026-03-16 18:32:19.729287-04	standard
25f08adb-c85d-4383-b810-431599befa8a	83c13b94-bd1f-4722-b2c0-542d6e47d89c	M3	t	2026-03-16 18:32:19.729459-04	ev
37660e47-a7f5-4a42-ac0e-d9003e2ff8a2	83c13b94-bd1f-4722-b2c0-542d6e47d89c	M4	t	2026-03-16 18:32:19.729643-04	accessible
8f5d869d-552e-4416-a18c-a395b08dc414	83c13b94-bd1f-4722-b2c0-542d6e47d89c	M5	f	2026-03-16 18:32:19.729873-04	standard
8819fc9f-5978-4ea5-9bb6-95f80c2ebdd9	3d43d3e1-70c2-41ee-a9b4-8feb00d8f4a1	H1	t	2026-03-16 18:32:19.730112-04	standard
369736bd-5c58-4dd0-b914-6ea94a4d9888	3d43d3e1-70c2-41ee-a9b4-8feb00d8f4a1	H2	t	2026-03-16 18:32:19.730337-04	standard
7d4ca35d-6175-474b-a0ab-675b5a58793d	3d43d3e1-70c2-41ee-a9b4-8feb00d8f4a1	H3	t	2026-03-16 18:32:19.730533-04	ev
b2ed9466-008f-459d-a20a-7589759c7ff9	3d43d3e1-70c2-41ee-a9b4-8feb00d8f4a1	H4	t	2026-03-16 18:32:19.730779-04	accessible
a6c85319-7707-4f85-a5c3-5d42ce2b22f0	3d43d3e1-70c2-41ee-a9b4-8feb00d8f4a1	H5	t	2026-03-16 18:32:19.730963-04	standard
efb65f28-cf2f-45d1-9b9f-c7f5aa164501	9fe19b35-18cf-467a-b78f-8502fca15ca5	J1	t	2026-03-16 18:32:19.731168-04	ev
c021cd0e-df5e-4be1-95fa-af3a7e248282	9fe19b35-18cf-467a-b78f-8502fca15ca5	J2	t	2026-03-16 18:32:19.731377-04	ev
e1b44413-78ca-4230-b770-4a74cd74f4ee	9fe19b35-18cf-467a-b78f-8502fca15ca5	J3	t	2026-03-16 18:32:19.731579-04	standard
d3f302ea-288f-4765-bf2e-8e84df0eee5a	9fe19b35-18cf-467a-b78f-8502fca15ca5	J4	t	2026-03-16 18:32:19.73178-04	accessible
53b3f605-47f6-4606-afec-eab5ed8a4c17	9fe19b35-18cf-467a-b78f-8502fca15ca5	J5	f	2026-03-16 18:32:19.731978-04	standard
cafd7fc1-9c60-4a6b-aa44-3517b7e0a866	b475f0db-9849-475f-8b09-4c85527acb90	N1	t	2026-03-16 18:32:19.732174-04	standard
c6608ad3-c2b4-4ee5-8dca-cd12bc858dcb	b475f0db-9849-475f-8b09-4c85527acb90	N2	t	2026-03-16 18:32:19.732381-04	standard
9c9c516e-4a5d-48ec-9e80-72f39999c928	b475f0db-9849-475f-8b09-4c85527acb90	N3	t	2026-03-16 18:32:19.732581-04	accessible
2411e68d-6746-4221-b143-8165e910a669	b475f0db-9849-475f-8b09-4c85527acb90	N4	t	2026-03-16 18:32:19.732779-04	ev
6628e2e0-fcce-46b5-bc1b-7f7c98d9764e	b475f0db-9849-475f-8b09-4c85527acb90	N5	t	2026-03-16 18:32:19.732986-04	standard
aa453c73-0565-4f00-8324-05d50a548fd1	269858c5-ba7d-4c3f-b267-9ce006290c91	E1	t	2026-03-16 18:32:19.733186-04	standard
079c361c-9441-4a11-ba23-e12918022e6c	269858c5-ba7d-4c3f-b267-9ce006290c91	E2	t	2026-03-16 18:32:19.733383-04	standard
aea3048b-3f70-4596-944e-b752d9d1492d	269858c5-ba7d-4c3f-b267-9ce006290c91	E3	t	2026-03-16 18:32:19.733593-04	ev
543424bd-70c5-47a5-803c-263f42f88478	269858c5-ba7d-4c3f-b267-9ce006290c91	E4	t	2026-03-16 18:32:19.733798-04	accessible
623e377b-5c20-42d6-80b4-6c5023514493	269858c5-ba7d-4c3f-b267-9ce006290c91	E5	t	2026-03-16 18:32:19.734005-04	standard
\.


--
-- Data for Name: password_resets; Type: TABLE DATA; Schema: public; Owner: vyomraj
--

COPY public.password_resets (id, user_id, token, expires_at, used, created_at) FROM stdin;
\.


--
-- Data for Name: profiles; Type: TABLE DATA; Schema: public; Owner: vyomraj
--

COPY public.profiles (id, user_id, full_name, phone, created_at, updated_at) FROM stdin;
033d4c7d-a26b-469a-9521-519bc9ab4bd6	cd444d86-d309-456d-aacc-7e7c3180b8b7	Daanish Shaikh	8624408424	2026-03-16 18:59:01.940048-04	2026-03-16 18:59:01.940048-04
\.


--
-- Data for Name: reservations; Type: TABLE DATA; Schema: public; Owner: vyomraj
--

COPY public.reservations (id, user_id, slot_id, start_time, end_time, status, created_at) FROM stdin;
28517b13-7d0f-477a-b1ad-e72e724043ae	f5d5dedd-d4a5-4ace-80fa-a56e8c84b306	126c2cc3-6e9f-47eb-8f92-714288ddd05a	2026-03-16 19:32:19.734222-04	2026-03-16 20:32:19.734222-04	CONFIRMED	2026-03-16 18:32:19.734222-04
e1571ad9-6b65-41ad-92cc-4f1e34041e80	f5d5dedd-d4a5-4ace-80fa-a56e8c84b306	7d4ca35d-6175-474b-a0ab-675b5a58793d	2026-03-16 19:02:19.738851-04	2026-03-16 20:02:19.738851-04	CONFIRMED	2026-03-16 18:32:19.738851-04
e1581117-6ea5-4c72-b185-33087fc3a0af	f5d5dedd-d4a5-4ace-80fa-a56e8c84b306	c021cd0e-df5e-4be1-95fa-af3a7e248282	2026-03-16 20:32:19.739235-04	2026-03-16 22:32:19.739235-04	CONFIRMED	2026-03-16 18:32:19.739235-04
feaac07a-db7c-4d13-b508-98351d994de8	f5d5dedd-d4a5-4ace-80fa-a56e8c84b306	2411e68d-6746-4221-b143-8165e910a669	2026-03-17 18:32:19.739555-04	2026-03-17 20:32:19.739555-04	CONFIRMED	2026-03-16 18:32:19.739555-04
384a6b0e-d6d1-492c-8fcc-481f90ef7b9c	f5d5dedd-d4a5-4ace-80fa-a56e8c84b306	aea3048b-3f70-4596-944e-b752d9d1492d	2026-03-16 21:32:19.739875-04	2026-03-16 23:32:19.739875-04	CONFIRMED	2026-03-16 18:32:19.739875-04
74ddfac3-2b82-4dad-a38f-64c234b25fef	cd444d86-d309-456d-aacc-7e7c3180b8b7	126c2cc3-6e9f-47eb-8f92-714288ddd05a	2026-03-16 20:36:00-04	2026-03-16 21:36:00-04	CONFIRMED	2026-03-16 18:36:50.481612-04
f528e849-ae0d-4e5e-a02b-250d5ac87283	cd444d86-d309-456d-aacc-7e7c3180b8b7	37660e47-a7f5-4a42-ac0e-d9003e2ff8a2	2026-03-16 18:48:00-04	2026-03-16 19:48:00-04	CONFIRMED	2026-03-16 18:47:50.150251-04
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: vyomraj
--

COPY public.users (id, email, password_hash, role, created_at) FROM stdin;
f5d5dedd-d4a5-4ace-80fa-a56e8c84b306	test@example.com	x	user	2026-02-15 14:57:09.841232-05
c15665f5-f285-4709-b7e7-22390b50c20a	test2@example.com	x	user	2026-02-15 14:57:58.746033-05
cd444d86-d309-456d-aacc-7e7c3180b8b7	daanishdriver@spoton.com	scrypt:32768:8:1$QK4S1kKmpp0bq0SB$90694805e31c76a4808ebddfa4e67e683ea5bbce61f360cb3776efb3d5ac79b627dab9fd84451183f5e327f0f265113f57196ba631ef664c23c61ba06b21d8fb	user	2026-03-16 13:18:44.164718-04
d25038e3-b154-4d25-b3ff-8b5765b06ea7	vyomoperator@spoton.com	scrypt:32768:8:1$CStHnfeHVUSAvc8K$62754a2d21641bf1fdc2e2b716dd04d3a77ed695e8eeb45e14e3180e20dce4dfb09778f51ba795de0be831d80a2428b5521a689812d4cb852eaf2193a8b0346a	admin	2026-03-16 13:19:35.190417-04
\.


--
-- Data for Name: vehicles; Type: TABLE DATA; Schema: public; Owner: vyomraj
--

COPY public.vehicles (id, user_id, plate_number, vehicle_make, vehicle_model, vehicle_color, created_at) FROM stdin;
93c81f8e-89e4-45fa-89b8-58e4f2f84d0a	cd444d86-d309-456d-aacc-7e7c3180b8b7	DAN0416	BMW	M5	Red	2026-03-16 19:00:15.300754-04
\.


--
-- Name: favorite_locations favorite_locations_pkey; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.favorite_locations
    ADD CONSTRAINT favorite_locations_pkey PRIMARY KEY (id);


--
-- Name: favorite_locations favorite_locations_user_id_parking_lot_id_key; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.favorite_locations
    ADD CONSTRAINT favorite_locations_user_id_parking_lot_id_key UNIQUE (user_id, parking_lot_id);


--
-- Name: parking_lots parking_lots_pkey; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.parking_lots
    ADD CONSTRAINT parking_lots_pkey PRIMARY KEY (id);


--
-- Name: parking_slots parking_slots_pkey; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.parking_slots
    ADD CONSTRAINT parking_slots_pkey PRIMARY KEY (id);


--
-- Name: password_resets password_resets_pkey; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_pkey PRIMARY KEY (id);


--
-- Name: password_resets password_resets_token_key; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_token_key UNIQUE (token);


--
-- Name: profiles profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_pkey PRIMARY KEY (id);


--
-- Name: profiles profiles_user_id_key; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_user_id_key UNIQUE (user_id);


--
-- Name: reservations reservations_no_overlap_confirmed; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_no_overlap_confirmed EXCLUDE USING gist (slot_id WITH =, tstzrange(start_time, end_time, '[)'::text) WITH &&) WHERE ((status = 'CONFIRMED'::text));


--
-- Name: reservations reservations_pkey; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_pkey PRIMARY KEY (id);


--
-- Name: parking_slots unique_slot_label_per_lot; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.parking_slots
    ADD CONSTRAINT unique_slot_label_per_lot UNIQUE (lot_id, label);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: vehicles vehicles_pkey; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_pkey PRIMARY KEY (id);


--
-- Name: vehicles vehicles_user_id_plate_number_key; Type: CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_user_id_plate_number_key UNIQUE (user_id, plate_number);


--
-- Name: idx_parking_slots_lot_id; Type: INDEX; Schema: public; Owner: vyomraj
--

CREATE INDEX idx_parking_slots_lot_id ON public.parking_slots USING btree (lot_id);


--
-- Name: idx_reservations_slot_id; Type: INDEX; Schema: public; Owner: vyomraj
--

CREATE INDEX idx_reservations_slot_id ON public.reservations USING btree (slot_id);


--
-- Name: idx_reservations_slot_time; Type: INDEX; Schema: public; Owner: vyomraj
--

CREATE INDEX idx_reservations_slot_time ON public.reservations USING btree (slot_id, start_time, end_time);


--
-- Name: idx_reservations_user_id; Type: INDEX; Schema: public; Owner: vyomraj
--

CREATE INDEX idx_reservations_user_id ON public.reservations USING btree (user_id);


--
-- Name: favorite_locations favorite_locations_parking_lot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.favorite_locations
    ADD CONSTRAINT favorite_locations_parking_lot_id_fkey FOREIGN KEY (parking_lot_id) REFERENCES public.parking_lots(id) ON DELETE CASCADE;


--
-- Name: favorite_locations favorite_locations_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.favorite_locations
    ADD CONSTRAINT favorite_locations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: parking_slots parking_slots_lot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.parking_slots
    ADD CONSTRAINT parking_slots_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES public.parking_lots(id) ON DELETE CASCADE;


--
-- Name: password_resets password_resets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: profiles profiles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: reservations reservations_slot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_slot_id_fkey FOREIGN KEY (slot_id) REFERENCES public.parking_slots(id) ON DELETE CASCADE;


--
-- Name: reservations reservations_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: vehicles vehicles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vyomraj
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict g3vMCobYTx6Vf9nLiBI6CV3C5ewbngfYswqDT7XbVkLJvFOgsIAf1dDjM4BVIa5

