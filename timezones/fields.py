from django.conf import settings
from django.db import models
from django.db.models import signals
from django.utils.encoding import smart_unicode, smart_str

import pytz

from timezones import forms, zones
from timezones.utils import coerce_timezone_value, validate_timezone_max_length



MAX_TIMEZONE_LENGTH = getattr(settings, "MAX_TIMEZONE_LENGTH", 100)
default_tz = pytz.timezone(getattr(settings, "TIME_ZONE", "UTC"))
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


class LocalizedDateTimeField(models.DateTimeField):
    """
    A model field that provides automatic localized timezone support.
    timezone can be a timezone string, a callable (returning a timezone string),
    or a queryset keyword relation for the model, or a pytz.timezone()
    result.
    """
    def __init__(self, verbose_name=None, name=None, timezone=None, **kwargs):
        if isinstance(timezone, basestring):
            timezone = smart_str(timezone)
        if timezone in pytz.all_timezones_set:
            self.timezone = pytz.timezone(timezone)
        else:
            self.timezone = timezone
        super(LocalizedDateTimeField, self).__init__(verbose_name, name, **kwargs)
    
    def formfield(self, **kwargs):
        defaults = {"form_class": forms.LocalizedDateTimeField}
        if (not isinstance(self.timezone, basestring) and str(self.timezone) in pytz.all_timezones_set):
            defaults["timezone"] = str(self.timezone)
        defaults.update(kwargs)
        return super(LocalizedDateTimeField, self).formfield(**defaults)
    
    def get_db_prep_save(self, value, connection):
        """
        Returns field's value prepared for saving into a database.
        """
        ## convert to db_tz
        if value is not None:
            if value.tzinfo is None:
                value = default_tz.localize(value).astimezone(db_tz)
            else:
                value = value.astimezone(db_tz)
        return super(LocalizedDateTimeField, self).get_db_prep_save(value)
    
    def get_db_prep_lookup(self, lookup_type, value):
        """
        Returns field's value prepared for database lookup.
        """
        ## convert to db_tz
        if value.tzinfo is None:
            value = default_tz.localize(value).astimezone(db_tz)
        else:
            value = value.astimezone(db_tz)
        return super(LocalizedDateTimeField, self).get_db_prep_lookup(lookup_type, value)

def prep_localized_datetime(sender, **kwargs):
    for field in sender._meta.fields:
        if not isinstance(field, LocalizedDateTimeField):# or field.timezone is None:
            continue

        # Beware python scoping and for loops (for loops don't introduce new scope)
        # http://stackoverflow.com/questions/233673/lexical-closures-in-python
        def gen_getset_dtz_field(field):
            dt_field_name = "_timezone_%s" % field.attname

            def get_dtz_field(instance):
                return getattr(instance, dt_field_name)

            def set_dtz_field(instance, dt):
                if dt is None: # Allow None here (db will catch later for null=False)
                    setattr(instance, dt_field_name, None)
                    return
                if dt.tzinfo is None:
                    dt = db_tz.localize(dt)
                time_zone = field.timezone
                if time_zone:
                    if isinstance(time_zone, basestring):
                        tz_name = instance._default_manager.filter(
                                pk=instance._get_pk_val()
                            ).values_list(field.timezone)[0][0]
                        try:
                            time_zone = pytz.timezone(tz_name)
                        except:
                            time_zone = default_tz
                        if time_zone is None:
                            # lookup failed
                            time_zone = default_tz
                            #raise pytz.UnknownTimeZoneError(
                            #    "Time zone %r from relation %r was not found"
                            #    % (tz_name, field.timezone)
                            #)
                    elif callable(time_zone):
                        tz_name = time_zone()
                        if isinstance(tz_name, basestring):
                            try:
                                time_zone = pytz.timezone(tz_name)
                            except:
                                time_zone = default_tz
                        else:
                            time_zone = tz_name
                        if time_zone is None:
                            # lookup failed
                            time_zone = default_tz
                            #raise pytz.UnknownTimeZoneError(
                            #    "Time zone %r from callable %r was not found"
                            #    % (tz_name, field.timezone)
                            #)
                    setattr(instance, dt_field_name, dt.astimezone(time_zone))
                else:
                    setattr(instance, dt_field_name, dt)
            return (get_dtz_field, set_dtz_field)
        setattr(sender, field.attname, property(*gen_getset_dtz_field(field)))

## RED_FLAG: need to add a check at manage.py validation time that
##           time_zone value is a valid query keyword (if it is one)
signals.class_prepared.connect(prep_localized_datetime)


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
