# -*- encoding:utf-8 -*-
from mako import runtime, filters, cache
UNDEFINED = runtime.UNDEFINED
__M_dict_builtin = dict
__M_locals_builtin = locals
_magic_number = 5
_modified_time = 1291063172.4809999
_template_filename='C:\\dev\\abstrackr_web\\abstrackr\\abstrackr\\templates/reviews/join_a_review.mako'
_template_uri='/reviews/join_a_review.mako'
_template_cache=cache.Cache(__name__, _modified_time)
_source_encoding='utf-8'
from webhelpers.html import escape
_exports = ['title']


def _mako_get_namespace(context, name):
    try:
        return context.namespaces[(__name__, name)]
    except KeyError:
        _mako_generate_namespaces(context)
        return context.namespaces[(__name__, name)]
def _mako_generate_namespaces(context):
    pass
def _mako_inherit(template, context):
    _mako_generate_namespaces(context)
    return runtime._inherit_from(context, u'../site.mako', _template_uri)
def render_body(context,**pageargs):
    context.caller_stack._push_frame()
    try:
        __M_locals = __M_dict_builtin(pageargs=pageargs)
        url = context.get('url', UNDEFINED)
        c = context.get('c', UNDEFINED)
        __M_writer = context.writer()
        # SOURCE LINE 1
        __M_writer(u'\r\n')
        # SOURCE LINE 2
        __M_writer(u'\r\n\r\n\r\n\r\n<h1>existing reviews</h1>\r\n<center>\r\n<div class="content">\r\nclick the link to join.<br/>\r\n')
        # SOURCE LINE 10
        for review in c.all_reviews:
            # SOURCE LINE 11
            __M_writer(u'    <a href = "')
            __M_writer(escape(url(controller='review', action='join_review', id=review.review_id)))
            __M_writer(u'">')
            __M_writer(escape(review.name))
            __M_writer(u'</a> <br/>\r\n')
            pass
        # SOURCE LINE 13
        __M_writer(u'</div>\r\n\r\n</center>')
        return ''
    finally:
        context.caller_stack._pop_frame()


def render_title(context):
    context.caller_stack._push_frame()
    try:
        __M_writer = context.writer()
        # SOURCE LINE 2
        __M_writer(u'new review')
        return ''
    finally:
        context.caller_stack._pop_frame()


