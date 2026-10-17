-- Tablas de la app Facturas → Marangatu.
-- Ejecutar una sola vez en Supabase: panel del proyecto → SQL Editor → pegar todo → Run.
-- Se puede volver a ejecutar sin perder datos.

create table if not exists usuarios (
    id bigint generated always as identity primary key,
    usuario text unique not null,
    nombre text not null,
    hash text not null,
    sal text not null,
    es_admin boolean not null default false,
    activo boolean not null default true
);

create table if not exists clientes (
    id bigint generated always as identity primary key,
    ruc text unique not null,
    dv text not null,
    nombre text not null,
    imputa_iva integer not null default 1,
    imputa_ire integer not null default 0,
    imputa_irp integer not null default 0,
    activo integer not null default 1
);

create table if not exists lotes (
    id bigint generated always as identity primary key,
    cliente_id bigint not null references clientes(id),
    tipo text not null,
    periodo text not null,
    archivos jsonb not null default '[]',
    creado_por text not null,
    creado_en timestamptz not null default now(),
    estado text not null default 'PROCESANDO'
);

create table if not exists paginas (
    id bigint generated always as identity primary key,
    lote_id bigint not null references lotes(id),
    nro integer not null,
    archivo text not null,
    ruta text,                         -- ruta en Supabase Storage; null cuando la imagen ya se borró
    comprobante_ref bigint,            -- página que continúa un comprobante de la página anterior
    resultado jsonb,
    error text
);

create table if not exists comprobantes (
    id bigint generated always as identity primary key,
    lote_id bigint not null references lotes(id),
    cliente_id bigint not null references clientes(id),
    pagina_id bigint references paginas(id),
    tipo_lote text not null,
    periodo text not null,
    clase text,
    tipo_comprobante integer,
    cdc text,
    ruc text, dv text, razon_social text,
    ruc_receptor text,
    timbrado text, vigencia_inicio text, vigencia_fin text,
    numero text, fecha text,
    gravada_10 bigint default 0, gravada_5 bigint default 0, exenta bigint default 0, total bigint default 0,
    iva_10 bigint default 0, iva_5 bigint default 0,
    condicion integer, moneda text default 'PYG',
    imputa_iva integer, imputa_ire integer, imputa_irp integer,
    estado text not null,
    exportado boolean not null default false,
    errores jsonb not null default '[]',
    campos_dudosos jsonb not null default '[]',
    observaciones text default '',
    actualizado_por text, actualizado_en timestamptz
);
-- Fase 2 (ventas): tipo de identificación de la contraparte (11 RUC, 12 cédula, 15 sin nombre…).
alter table comprobantes add column if not exists tipo_identificacion text not null default '11';
-- Resumen para el sistema contable (Contadis): concepto breve de lo comprado o vendido.
alter table comprobantes add column if not exists concepto text not null default '';

create index if not exists ix_comp_periodo on comprobantes(cliente_id, tipo_lote, periodo);
create index if not exists ix_comp_clave on comprobantes(cliente_id, ruc, timbrado, numero);
create index if not exists ix_comp_pagina on comprobantes(pagina_id);

create table if not exists historial (
    id bigint generated always as identity primary key,
    comprobante_id bigint not null references comprobantes(id),
    usuario text not null,
    fecha timestamptz not null default now(),
    accion text not null,
    detalle jsonb
);

create table if not exists exportaciones (
    id bigint generated always as identity primary key,
    cliente_id bigint not null references clientes(id),
    tipo_lote text not null,
    periodo text not null,
    secuencia integer not null,
    archivo text not null,
    cantidad integer not null,
    usuario text not null,
    fecha timestamptz not null default now()
);

-- Seguridad: Supabase publica las tablas por su API REST. Se activa RLS sin políticas para
-- que nadie pueda leerlas por esa vía; la app se conecta directo a la base y no se ve afectada.
alter table usuarios      enable row level security;
alter table clientes      enable row level security;
alter table lotes         enable row level security;
alter table paginas       enable row level security;
alter table comprobantes  enable row level security;
alter table historial     enable row level security;
alter table exportaciones enable row level security;

-- Carpeta privada para las imágenes de las facturas (se borran al aprobar o exportar).
insert into storage.buckets (id, name, public)
values ('facturas', 'facturas', false)
on conflict (id) do nothing;
