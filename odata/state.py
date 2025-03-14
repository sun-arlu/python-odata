# -*- coding: utf-8 -*-

from __future__ import print_function

import inspect
import itertools
from collections import OrderedDict
from typing import Optional

import rich
import rich.panel
import rich.table

from odata.flags import ODataServerFlags
from odata.property import PropertyBase, NavigationProperty


ETagUnsupported = object()
"""Use if the API response does not contain ETags."""


class EntityState(object):

    def __init__(self, entity):
        """:type entity: EntityBase """
        self.entity: "EntityBase" = entity
        self.dirty = []
        self.nav_cache = {}
        self.data = {}
        self.etag = None
        self.connection = None
        self.parent_navigation_url: Optional[str] = None  # for chaining objects, like OrderDetails.Order.Employee

    @property
    def persisted(self):
        return self.etag is not None

    # dictionary access
    def __getitem__(self, item):
        return self.data[item]

    def __setitem__(self, key, value):
        self.data[key] = value

    def __contains__(self, item):
        return item in self.data

    def get(self, key, default):
        return self.data.get(key, default=default)

    def update(self, other):
        self.data.update(other)
        self.etag = other.get("@odata.etag")
    # /dictionary access

    def __repr__(self):
        return self.data.__repr__()

    def values(self):
        title = f"{self.entity.__odata_type__}"
        table = rich.table.Table(
            rich.table.Column("Properties", header_style="bold"),
            rich.table.Column("Values", header_style="bold"),
            row_styles=["dim", "none"],
            min_width=len(title),
            title=title)

        show_properties = []
        values = []
        for key, prop in self.properties:
            name = prop.name
            if prop.is_collection:
                name += "[]"
            if prop.primary_key:
                name += '*'
            if prop.name in self.dirty:
                name += ' (dirty)'
            show_properties.append(name)
            values.append(str(self.data[key]))
        for items in itertools.zip_longest(show_properties, values, fillvalue=""):
            table.add_row(*items)

        rich.print(table)

    def describe(self):
        table = rich.table.Table(
            rich.table.Column("Properties", header_style="bold"),
            # rich.table.Column(header="Property type", header_style="bold blue", style="dim blue"),
            rich.table.Column("Navigation properties", header_style="bold"),
            # rich.table.Column(header="Navigation property type", header_style="bold blue", style="dim blue"),
            row_styles=["dim", "none"],
            title='EntitySet: [red]{0}[/red]'.format(self.entity.__odata_collection__))

        panel = rich.panel.Panel(table,
            title=f"[green]{self.entity.__odata_type__}",
            subtitle=f"URL={self.instance_url or self.entity.__odata_url__()}", expand=False)

        show_properties = []
        # show_types = []
        for _, prop in self.properties:
            name = prop.name
            if prop.is_collection:
                name += "[]"
            if prop.primary_key:
                name += '*'
            if prop.name in self.dirty:
                name += ' (dirty)'

            show_properties.append(
                rich.console.Text.assemble(rich.console.Text(name), ": ", rich.console.Text(type(prop).__name__, style="dim blue", overflow="ellipsis"))
            )
            # show_types.append(type(prop).__name__)

        show_nav_properties = []
        # show_nav_types = []
        for _, prop in self.navigation_properties:
            name = prop.name
            if prop.is_collection:
                name += "[]"
            show_nav_properties.append(
                rich.console.Text.assemble(name, ": ", rich.console.Text(prop.entitycls.__name__, style="dim blue"), overflow="ellipsis"))

            # show_nav_types.append(prop.entitycls.__name__)

        for items in itertools.zip_longest(show_properties, show_nav_properties, fillvalue=""):
        # for items in itertools.zip_longest(show_properties, show_types, show_nav_properties, show_nav_types, fillvalue=""):
            table.add_row(*items)

        rich.print(panel)
        return table

    def reset(self):
        self.dirty = []
        self.nav_cache = {}

    @property
    def id(self):
        __missing__ = object()
        ids = []
        entity_name = self.entity.__odata_collection__
        if entity_name is None:
            return

        for prop_name, prop in self.primary_key_properties:
            value = self.data.get(prop.name, __missing__)
            if value is not __missing__:
                ids.append((prop, str(prop.escape_value(value))))
        if len(ids) == 1:
            key_value = ids[0][1]
            return u'{0}({1})'.format(entity_name,
                                      key_value)
        if len(ids) > 1:
            key_ids = []
            for prop, key_value in ids:
                key_ids.append('{0}={1}'.format(prop.name, key_value))
            return u'{0}({1})'.format(entity_name, ','.join(key_ids))

    @property
    def instance_url(self):
        if self.id:
            return self.entity.__odata_url_base__ + self.id

    @property
    def properties(self):
        props = []
        cls = self.entity.__class__
        for key, value in cls.__odata_props__:
            if isinstance(value, PropertyBase):
                props.append((key, value))
        return props

    @property
    def primary_key_properties(self):
        pks = []
        for prop_name, prop in self.properties:
            if prop.primary_key is True:
                pks.append((prop_name, prop))
        return pks

    @property
    def navigation_properties(self):
        props = []
        cls = self.entity.__class__
        for key, value in inspect.getmembers(cls):
            if isinstance(value, NavigationProperty):
                props.append((key, value))
        return props

    @property
    def dirty_properties(self):
        rv = []
        for prop_name, prop in self.properties:
            if prop.name in self.dirty:
                rv.append((prop_name, prop))
        return rv

    def _format_odata_bind_key(self, prop_name, require_slash: bool = False):
        key = '{0}@odata.bind'.format(prop_name)
        key = f'/{key}' if require_slash else key
        return key

    def set_property_dirty(self, prop):
        if prop.name not in self.dirty:
            self.dirty.append(prop.name)

    def data_for_insert(self, server_flags: ODataServerFlags):
        return self._clean_new_entity(self.entity, server_flags)

    def data_for_update(self, server_flags: ODataServerFlags):
        update_data = OrderedDict()
        if server_flags.provide_odata_type_annotation:
            update_data['@odata.type'] = self.entity.__odata_type__

        for _, prop in self.dirty_properties:
            if prop.is_computed_value:
                continue

            update_data[prop.name] = self.data[prop.name]

        for prop_name, prop in self.navigation_properties:
            if prop.name in self.dirty:
                value = getattr(self.entity, prop_name, None)  # get the related object
                """:type : None | odata.entity.EntityBase | list[odata.entity.EntityBase]"""
                if value is not None:
                    key = self._format_odata_bind_key(prop.name, server_flags.odata_bind_requires_slash)
                    if prop.is_collection:
                        update_data[key] = [i.__odata__.id for i in value]
                    else:
                        update_data[key] = value.__odata__.id

        if server_flags.skip_null_properties:
            update_data = _remove_null_properties(update_data)

        return update_data

    def _clean_new_entity(self, entity, server_flags: ODataServerFlags):
        """:type entity: odata.entity.EntityBase """
        insert_data = OrderedDict()
        if server_flags.provide_odata_type_annotation:
            insert_data['@odata.type'] = entity.__odata_type__

        es = entity.__odata__
        for _, prop in es.properties:
            if prop.is_computed_value:
                continue

            insert_data[prop.name] = es[prop.name]

        # Allow pk properties only if they have values
        for _, pk_prop in es.primary_key_properties:
            if insert_data[pk_prop.name] is None:
                insert_data.pop(pk_prop.name)

        # Deep insert from nav properties
        for prop_name, prop in es.navigation_properties:
            if prop.foreign_key:
                insert_data.pop(prop.foreign_key, None)

            value = getattr(entity, prop_name, None)
            """:type : None | odata.entity.EntityBase | list[odata.entity.EntityBase]"""
            if value is not None:

                if prop.is_collection:
                    binds = []

                    # binds must be added first
                    for i in [i for i in value if i.__odata__.id]:
                        binds.append(i.__odata__.id)

                    if len(binds):
                        key = self._format_odata_bind_key(prop.name, server_flags.odata_bind_requires_slash)
                        insert_data[key] = binds

                    new_entities = []
                    for i in [i for i in value if i.__odata__.id is None]:
                        new_entities.append(self._clean_new_entity(i, server_flags))

                    if len(new_entities):
                        insert_data[prop.name] = new_entities

                else:
                    if value.__odata__.id:
                        key = self._format_odata_bind_key(prop.name, server_flags.odata_bind_requires_slash)
                        insert_data[key] = value.__odata__.id
                    else:
                        insert_data[prop.name] = self._clean_new_entity(value, server_flags)

        if server_flags.skip_null_properties:
            insert_data = _remove_null_properties(insert_data)

        return insert_data

def _remove_null_properties(data):
    for key in [key for key, value in data.items() if value is None]:
        del data[key]
    return data
