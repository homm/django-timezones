# -*- coding: utf-8 -*-

from django.conf import settings
from django.db import models
from django.db.models import signals, fields
from django.utils.encoding import smart_unicode, smart_str
from django.utils.functional import curry

import pytz
from datetime import datetime, tzinfo

from timezones import forms, zones
from timezones.utils import coerce_timezone_value, validate_timezone_max_length

MAX_TIMEZONE_LENGTH = getattr(settings, "MAX_TIMEZONE_LENGTH", 100)
" All LocalizedDateTimeField have own time zone. If this time zone is not set, default_tz is used. "
default_tz = pytz.timezone(getattr(settings, "TIME_ZONE", "UTC"))
" All data in database stored in UTC. "
db_tz = pytz.utc

class TimeZoneField(models.CharField):
    
    __metaclass__ = models.SubfieldBase
    _south_introspects = True
    
    def __init__(self, *args, **kwargs):
        validate_timezone_max_length(MAX_TIMEZONE_LENGTH, zones.ALL_TIMEZONE_CHOICES)
        defaults = {
            "max_length": MAX_TIMEZONE_LENGTH,
            "default": settings.TIME_ZONE,
            "choices": zones.PRETTY_TIMEZONE_CHOICES
        }
        defaults.update(kwargs)
        return super(TimeZoneField, self).__init__(*args, **defaults)
    
    def validate(self, value, instance):
        # coerce value back to a string to validate correctly
        return super(TimeZoneField, self).validate(smart_str(value), instance)
    
    def run_validators(self, value):
        # coerce value back to a string to validate correctly
        return super(TimeZoneField, self).run_validators(smart_str(value))
    
    def to_python(self, value):
        value = super(TimeZoneField, self).to_python(value)
        if value is None:
            return None # null=True
        return coerce_timezone_value(value)
    
    def get_prep_value(self, value):
        if value is not None:
            return smart_unicode(value)
        return value
    
    def get_db_prep_save(self, value, connection):
        """
        Prepares the given value for insertion into the database.
        """
        return self.get_prep_value(value)
    
    def flatten_data(self, follow, obj=None):
        value = self._get_val_from_obj(obj)
        if value is None:
            value = ""
        return {self.attname: smart_unicode(value)}


class TestDateTimeDescriptor(object):
    """
    Хитрость в том, что данные в базе хранятся в utc и минуя метод __set__ данного класса 
    падают точно в attname, который, ксати, обычно называется fieldname_utc.
    """
    def __init__(self, field):
        self.field = field
    
    def __set__(self, instance, value):
        """
        Метод __set__ вызывается только когда полю назаначаются данные из вне. А все данные из вне приходят 
        в часовом поясе данного конкретного поля. Но методу __set__ не нужно знать такие тонкости, ему 
        нужно только сохранить пришедшие данные в отличном от attname месте. Просто name подойдет.
        """
        instance.__dict__[self.field.name] = value
        cache_name = self.field.get_cache_name()
        if cache_name in instance.__dict__:
            del instance.__dict__[cache_name]
        if self.field.attname in instance.__dict__:
            del instance.__dict__[self.field.attname]
        
    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        
        cache_name = self.field.get_cache_name()
        if cache_name in instance.__dict__ and isinstance(instance.__dict__[cache_name], datetime):
            return instance.__dict__[cache_name]
        
        if self.field.attname in instance.__dict__:
            " В attname (fieldname_utc) всегда datetime в utc "
            value = instance.__dict__[self.field.attname]
            if not value is None:
                if value.tzinfo is None:
                   value = db_tz.localize(value)
                timezone = getattr(instance, self.field.get_timezone_name())
                value = value.astimezone(timezone)
            instance.__dict__[cache_name] = value
            return value
        else:
            value = instance.__dict__[self.field.name]
            if isinstance(value, datetime):
                " В просто name попадают данные в текущем часовом поясе поля. "
                timezone = getattr(instance, self.field.get_timezone_name())
                if value.tzinfo is None:
                    value = timezone.localize(value)
                else:
                    value = value.astimezone(timezone)
                instance.__dict__[cache_name] = value
            return value
    
    def get_for_save(self, instance):
        " Возвращает значение для базы, в utc "
        if self.field.attname in instance.__dict__:
            " В attname (fieldname_utc) всегда datetime в utc "
            value = instance.__dict__[self.field.attname]
            if not value is None and value.tzinfo is None:
               value = db_tz.localize(value)
            return value
        else:
            value = self.field.to_python(instance.__dict__[self.field.name])
            " В просто name попадают данные в текущем часовом поясе поля. "
            if not value is None:
                if value.tzinfo is None:
                    timezone = getattr(instance, self.field.get_timezone_name())
                    value = timezone.localize(value)
                value = value.astimezone(db_tz)
            return value

def get_timezone(instance, timezone, cache_name):
    """
    Функция навешивается на класс модели, только если часовой пояс callable или lookup.
    Если в кеше есть значение, у него есть timezone, значит можно взять его.
    В кеше может лежать как datetime, так и tzinfo.
    """
    if cache_name in instance.__dict__:
        value = instance.__dict__[cache_name]
        if isinstance(value, datetime):
            value = value.tzinfo
        return value
    
    if callable(timezone):
        " need call and converting "
        timezone = timezone()
    else:
        " need lookup in db "
        if not '__' in timezone:
            timezone = getattr(instance, timezone)
            if callable(timezone):
                " supports both fields and methods "
                timezone = timezone()
        else:
            timezone_parts = timezone.split('__')
            while True:
                part = '_%s_cache' % timezone_parts[0]
                if part in instance.__dict__:
                    instance = instance.__dict__[part]
                    timezone_parts = timezone_parts[1:]
                else:
                    break
            if len(timezone_parts) > 1:
                timezone = instance._default_manager.filter(
                    pk=instance._get_pk_val()
                ).values_list('__'.join(timezone_parts))[0][0]
            else:
                timezone = getattr(instance, timezone_parts[0])
            if timezone is None:
                timezone = default_tz
            print 'get_timezone: ', timezone_parts
    
    if isinstance(timezone, basestring):
        timezone = pytz.timezone(timezone)
    
    instance.__dict__[cache_name] = timezone
    return timezone 

class TestDateTimeField(models.DateTimeField):
    def __init__(self, verbose_name=None, name=None, timezone=None, **kwargs):
        """
        timezone - Может быть объектом tytz.timezone, или callable, возвращающим tytz.timezone, или строкой. 
            Если timezone строка — то это либо название часового пояса, которое должно присутствовать 
            в pytz.all_timezones_set, либо лукап в базу данных.
        """
        if isinstance(timezone, basestring):
            timezone = smart_str(timezone)
        if timezone in pytz.all_timezones_set:
            self.timezone = pytz.timezone(timezone)
        else:
            self.timezone = timezone
        super(TestDateTimeField, self).__init__(verbose_name, name, **kwargs)
        
    def get_prep_value(self, value):
        """
        Все данные хранятся в utc. Соответствено, прежде чем обращатся к серверу, нужно сконверитровать все в utc.
        Данные с неизвестной зоной нужно считать зоной приложения, т.к. они скорее всего получены от 
        datetime.now() или производных. Для прочих случаев лучше прописывать зону явно.
        """
        value = self.to_python(value)
        if not value is None:
            if value.tzinfo is None:
                value = default_tz.localize(value)
            value = value.astimezone(db_tz)
        return value
    
    def get_db_prep_value(self, value, connection, prepared=False):
        """
        Драйвер mysql (может быть не только он) отказывается принимать данные tzinfo, поэтому tzinfo нужно вырезать. 
        """
        if not prepared:
            value = self.get_prep_value(value)
        if not value is None and connection.settings_dict['ENGINE'] in ('django.db.backends.mysql',):
            value = value.replace(tzinfo=None)
        return connection.ops.value_to_db_datetime(value)
    
    def pre_save(self, model_instance, add):
        if self.auto_now or (self.auto_now_add and add):
            " Во-первых, текущее время нужно взять в utc "
            value = datetime.now(db_tz)
            setattr(model_instance, self.name, value)
            return value
        """
        Во-вторых, нужно уже здесь подготовить значение, потому что дальше не 
        будет доступа к модели, а значит и к часовому поясу.
        """
        descriptor = getattr(type(model_instance), self.name)
        return descriptor.get_for_save(model_instance)
    
    def get_db_prep_save(self, value, connection):
        " Значение было подготовленно выше, в pre_save "
        return self.get_db_prep_value(value, connection=connection, prepared=True)
    
    def get_default(self):
        """
        Когда вызывается get_defaults, объекта нет, поэтому считаем часовой пояс приложения.
        Default уже не может быть ничем кроме datetime или None.
        """
        value = super(TestDateTimeField, self).get_default()
        return self.get_prep_value(value)
    
    def get_attname(self):
        " Для хранения значения из базы в модели выбираем другое имя. "
        return '%s_utc' % self.name
    
    def get_timezone_name(self):
        return '%s_timezone' % self.name
    
    def contribute_to_class(self, cls, name):
        super(TestDateTimeField, self).contribute_to_class(cls, name)
        setattr(cls, name, TestDateTimeDescriptor(self))
        timezone = self.timezone
        if timezone is None:
            setattr(cls, self.get_timezone_name(), default_tz)
        elif isinstance(timezone, tzinfo):
            " already converted "
            setattr(cls, self.get_timezone_name(), timezone)
        else:
            setattr(cls, self.get_timezone_name(), property(curry(get_timezone, timezone=timezone, cache_name=self.get_cache_name())))

    def _get_val_from_obj(self, obj):
        " Используется модулем сериализации. Почему-то для сериализвации берется attname, а для десериализации просто name. "
        if obj is not None:
            descriptor = getattr(type(obj), self.name)
            return descriptor.__get__(model_instance)
        else:
            return self.get_default()
    
    def formfield(self, **kwargs):
        """
        Нужно запретить наследовать значение default, потому что для объекта оно должно быть в часовом поясе приложения,
        а значения в формах должны быть в поясе объекта. Для ModelForm нужно передавать instance, с которого будет браться
        актуальное значение. 
        """
        defaults = {'initial': None, 'show_hidden_initial': False}
        defaults.update(kwargs)
        return super(TestDateTimeField, self).formfield(**defaults)

# allow South to handle TimeZoneField smoothly
try:
    from south.modelsinspector import add_introspection_rules
    add_introspection_rules(
        rules=[
            (
                (TimeZoneField, ), 
                [], 
                {
                    "max_length": ["max_length", { "default": MAX_TIMEZONE_LENGTH }],
                }
            )
        ],
        patterns=['timezones\.fields\.']
    )
except ImportError:
    pass
