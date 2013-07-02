from __future__ import unicode_literals

import django
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.generic import GenericForeignKey
from django.db import models, IntegrityError, transaction
from django.db.models.query import QuerySet
from django.template.defaultfilters import slugify as default_slugify
from django.utils.translation import ugettext_lazy as _, ugettext
from django.utils.encoding import python_2_unicode_compatible


@python_2_unicode_compatible
class TagBase(models.Model):
    name = models.CharField(verbose_name=_('Name'), unique=True, max_length=100)
    slug = models.SlugField(verbose_name=_('Slug'), unique=True, max_length=100)

    def __str__(self):
        return self.name

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.pk and not self.slug:
            self.unique_slugify(self.name)
            super(TagBase, self).save(*args, **kwargs)
            
            #from django.db import router
            #using = kwargs.get("using") or router.db_for_write(
            #    type(self), instance=self)
            # Make sure we write to the same db for all attempted writes,
            # with a multi-master setup, theoretically we could try to
            # write and rollback on different DBs
            #kwargs["using"] = using
            #trans_kwargs = {"using": using}
            #i = 0
            #while True:
            #    i += 1
            #    try:
            #        sid = transaction.savepoint(**trans_kwargs)
            #        res = super(TagBase, self).save(*args, **kwargs)
            #        transaction.savepoint_commit(sid, **trans_kwargs)
            #        return res
            #    except IntegrityError:
            #        transaction.savepoint_rollback(sid, **trans_kwargs)
            #        self.slug = self.slugify(self.name, i)
        else:
            return super(TagBase, self).save(*args, **kwargs)

    def unique_slugify(self, value, slug_field_name='slug', queryset=None,
                   slug_separator='-'):
        """
        Calculates and stores a unique slug of ``value`` for an instance.
    
        ``slug_field_name`` should be a string matching the name of the field to
        store the slug in (and the field to check against for uniqueness).
    
        ``queryset`` usually doesn't need to be explicitly provided - it'll default
        to using the ``.all()`` queryset from the model's default manager.
        """
        slug_field = self._meta.get_field(slug_field_name)
    
        slug = getattr(self, slug_field.attname)
        slug_len = slug_field.max_length
    
        # Sort out the initial slug, limiting its length if necessary.
        slug = slugify(value)
        if slug_len:
            slug = slug[:slug_len]
        slug = self._slug_strip(slug, slug_separator)
        original_slug = slug
    
        # Create the queryset if one wasn't explicitly provided and exclude the
        # current instance from the queryset.
        if queryset is None:
            queryset = self.__class__._default_manager.all()
        if self.pk:
            queryset = queryset.exclude(pk=self.pk)
    
        # Find a unique slug. If one matches, at '-2' to the end and try again
        # (then '-3', etc).
        next = 2
        while not slug or queryset.filter(**{slug_field_name: slug}):
            slug = original_slug
            end = '%s%s' % (slug_separator, next)
            if slug_len and len(slug) + len(end) > slug_len:
                slug = slug[:slug_len-len(end)]
                slug = self._slug_strip(slug, slug_separator)
            slug = '%s%s' % (slug, end)
            next += 1
    
        setattr(self, slug_field.attname, slug)


    def _slug_strip(self, value, separator='-'):
        """
        Cleans up a slug by removing slug separator characters that occur at the
        beginning or end of a slug.
    
        If an alternate separator is used, it will also replace any instances of
        the default '-' separator with the new separator.
        """
        separator = separator or ''
        if separator == '-' or not separator:
            re_sep = '-'
        else:
            re_sep = '(?:-|%s)' % re.escape(separator)
        # Remove multiple instances and if an alternate separator is provided,
        # replace the default '-' separator.
        if separator != re_sep:
            value = re.sub('%s+' % re_sep, separator, value)
        # Remove separator from the beginning and end of the slug.
        if separator:
            if separator != '-':
                re_sep = re.escape(separator)
            value = re.sub(r'^%s+|%s+$' % (re_sep, re_sep), '', value)
        return value



class Tag(TagBase):
    class Meta:
        verbose_name = _("Tag")
        verbose_name_plural = _("Tags")


@python_2_unicode_compatible
class ItemBase(models.Model):
    def __str__(self):
        return ugettext("%(object)s tagged with %(tag)s") % {
            "object": self.content_object,
            "tag": self.tag
        }

    class Meta:
        abstract = True

    @classmethod
    def tag_model(cls):
        return cls._meta.get_field_by_name("tag")[0].rel.to

    @classmethod
    def tag_relname(cls):
        return cls._meta.get_field_by_name('tag')[0].rel.related_name

    @classmethod
    def lookup_kwargs(cls, instance):
        return {
            'content_object': instance
        }

    @classmethod
    def bulk_lookup_kwargs(cls, instances):
        return {
            "content_object__in": instances,
        }


class TaggedItemBase(ItemBase):
    tag = models.ForeignKey(Tag, related_name="%(app_label)s_%(class)s_items")

    class Meta:
        abstract = True

    @classmethod
    def tags_for(cls, model, instance=None):
        if instance is not None:
            return cls.tag_model().objects.filter(**{
                '%s__content_object' % cls.tag_relname(): instance
            })
        return cls.tag_model().objects.filter(**{
            '%s__content_object__isnull' % cls.tag_relname(): False
        }).distinct()


class GenericTaggedItemBase(ItemBase):
    object_id = models.IntegerField(verbose_name=_('Object id'), db_index=True)
    content_type = models.ForeignKey(
        ContentType,
        verbose_name=_('Content type'),
        related_name="%(app_label)s_%(class)s_tagged_items"
    )
    content_object = GenericForeignKey()

    class Meta:
        abstract=True

    @classmethod
    def lookup_kwargs(cls, instance):
        return {
            'object_id': instance.pk,
            'content_type': ContentType.objects.get_for_model(instance)
        }

    @classmethod
    def bulk_lookup_kwargs(cls, instances):
        if isinstance(instances, QuerySet):
            # Can do a real object_id IN (SELECT ..) query.
            return {
                "object_id__in": instances,
                "content_type": ContentType.objects.get_for_model(instances.model),
            }
        else:
            # TODO: instances[0], can we assume there are instances.
            return {
                "object_id__in": [instance.pk for instance in instances],
                "content_type": ContentType.objects.get_for_model(instances[0]),
            }

    @classmethod
    def tags_for(cls, model, instance=None):
        ct = ContentType.objects.get_for_model(model)
        kwargs = {
            "%s__content_type" % cls.tag_relname(): ct
        }
        if instance is not None:
            kwargs["%s__object_id" % cls.tag_relname()] = instance.pk
        return cls.tag_model().objects.filter(**kwargs).distinct()


class TaggedItem(GenericTaggedItemBase, TaggedItemBase):
    class Meta:
        verbose_name = _("Tagged Item")
        verbose_name_plural = _("Tagged Items")
