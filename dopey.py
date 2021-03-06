#!/usr/bin/env python
# -*- coding: utf-8 -*-
import yaml
import re
import datetime
import json
import argparse
from threading import Thread, Lock
import logging
import logging.handlers
import logging.config
import smtplib
from email.mime.text import MIMEText
import elasticsearch
import curator


import logging
import logging.config


def initlog(level=None, log="-"):
    if level is None:
        level = logging.DEBUG if __debug__ else logging.INFO
    if isinstance(level, basestring):
        level = getattr(logging, level.upper())

    class MyFormatter(logging.Formatter):

        def format(self, record):
            dformatter = '[%(asctime)s] %(levelname)s %(thread)d %(name)s %(pathname)s %(lineno)d - %(message)s'
            formatter = '[%(asctime)s] %(levelname)s %(thread)d %(name)s %(message)s'
            if record.levelno <= logging.DEBUG:
                self._fmt = dformatter
            else:
                self._fmt = formatter
            return super(MyFormatter, self).format(record)

    config = {
        "version": 1,
        "disable_existing_loggers": True,
        "formatters": {
            "custom": {
                '()': MyFormatter
            },
            "simple": {
                "format": "[%(asctime)s] %(levelname)s %(thread)d %(name)s %(message)s"
            },
            "verbose": {
                "format": "[%(asctime)s] %(levelname)s %(thread)d %(name)s %(pathname)s %(lineno)d - %(message)s"
            }
        },
        "handlers": {
        },
        'root': {
            'level': level,
            'handlers': ['console']
        }
    }
    console = {
        "class": "logging.StreamHandler",
        "level": "DEBUG",
        "formatter": "custom",
        "stream": "ext://sys.stdout"
    }
    file_handler = {
        "class": "logging.handlers.RotatingFileHandler",
        "level": "DEBUG",
        "formatter": "custom",
        "filename": log,
        "maxBytes": 10*1000**2,  # 10M
        "backupCount": 5,
        "encoding": "utf8"
    }
    if log == "-":
        config["handlers"]["console"] = console
        config["root"]["handlers"] = ["console"]
    else:
        config["handlers"]["file_handler"] = file_handler
        config["root"]["handlers"] = ["file_handler"]
    logging.config.dictConfig(config)


logger = None
lock = Lock()


class Sumary(object):

    def __init__(self):
        super(Sumary, self).__init__()
        self.records = []

    def add(self, record):
        self.records.append(
            '[%s] %s' %
            (datetime.datetime.now().strftime('%Y.%m.%d %H:%M:%S'), record))

    @property
    def sumary(self):
        return '\n'.join(self.records)

    def prints(self):
        print self.sumary.encode('utf-8')

    def log(self):
        logging.getLogger("DopeySumary").info(self.sumary)

    def mail(self, mail_host, from_who, to_list, sub="dopey summary"):
        content = self.sumary
        content = content.encode('utf-8')

        msg = MIMEText(content)
        msg['Subject'] = sub
        msg['From'] = from_who
        msg['To'] = ";".join(to_list)
        try:
            s = smtplib.SMTP()
            s.connect(mail_host)
            s.sendmail(from_who, to_list, msg.as_string())
            s.close()
        except Exception as e:
            logging.error(str(e))


dopey_summary = Sumary()

_delete = []
_close = []
_optimize = []
_dealt = []


def get_relo_index_cnt(esclient):
    cnt = elasticsearch.client.CatClient(esclient).health(h="relo")
    return int(cnt)


def update_cluster_settings(esclient, settings):
    """
    :type esclient: elasticsearch.Elasticsearch
    :type settings: cluster settings
    :rtype: response
    """
    logging.info('update cluster settings: %s' % settings)
    r = esclient.cluster.put_settings(settings, master_timeout='300s')
    logging.debug('update cluster response: %s' % r)
    return r


def delete_indices(esclient, indices, settings):
    """
    :type esclient: elasticsearch.Elasticsearch
    :type indices: list of (indexname,index_settings)
    :type settings: dict, not used
    :rtype: None
    """
    if not indices:
        return
    indices = [e[0] for e in indices]
    _delete.extend(indices)
    logger.debug("try to delete %s" % ','.join(indices))
    global lock
    with lock:
        for index in indices:
            if curator.delete_indices(esclient, [index], master_timeout='300s'):
                logger.info('%s deleted' % index)
                dopey_summary.add(u'%s 己删除' % index)
            else:
                logger.warn('%s deleted failed' % index)
                dopey_summary.add(u'%s 删除失败' % index)


def close_indices(esclient, indices, settings):
    """
    :type esclient: elasticsearch.Elasticsearch
    :type indices: list of (indexname,index_settings)
    :type settings: dict, not used
    :rtype: None
    """
    if not indices:
        return
    indices = [e[0] for e in indices]
    _close.extend(indices)
    logger.debug("try to close %s" % ','.join(indices))
    global lock
    with lock:
        for index in indices:
            if curator.close_indices(esclient, [index]):
                logger.info('%s closed' % index)
                dopey_summary.add(u'%s 已关闭' % index)
            else:
                logger.warn('%s closed failed' % index)
                dopey_summary.add(u'%s 关闭失败' % index)


def optimize_index(esclient, index, settings):
    dopey_summary.add(u"%s optimize 开始" % index)
    try:
        if curator.optimize_index(
                esclient,
                index,
                max_num_segments=settings.get("max_num_segments", 1),
                request_timeout=18 *
                3600):
            logger.info('%s optimized' % index)
            dopey_summary.add(u"%s optimize 完成" % index)
        else:
            raise
    except:
        logger.info(u"%s optimize 未完成退出" % index)
        dopey_summary.add(u"%s optimize 未完成退出" % index)


def optimize_indices(esclient, indices, settings):
    """
    :type esclient: elasticsearch.Elasticsearch
    :type indices: list of (indexname,index_settings)
    :type settings: dict, max_num_segments setting and so on
    :rtype: None
    """
    if not indices:
        return []

    indices = [e[0] for e in indices]
    _optimize.extend(indices)
    logger.debug("try to optimize %s" % ','.join(indices))

    for index in indices:
        optimize_index(esclient, index, settings)


def open_replic(esclient, indices, settings):
    """
    :type esclient: elasticsearch.Elasticsearch
    :type indices: list of (indexname,index_settings)
    :type settings: dict, not used
    :rtype: None
    """
    if not indices:
        return
    logger.debug("try to open replic, %s" % ','.join([e[0] for e in indices]))
    dopey_summary.add(u"%s 打开replic" % ",".join([e[0] for e in indices]))
    index_client = elasticsearch.client.IndicesClient(esclient)
    for index, index_settings in indices:
        replic = index_settings[index]['settings'][
            'index']['number_of_replicas']
        index_client.put_settings(
            index=index,
            body={"index.number_of_replicas": replic},
            params={'master_timeout': '300s'}
        )


def _compare_index_settings(part, whole):
    '''
    return True if part is part of whole
    type part: dict or else
    type whole: dict or else
    rtype: boolean
    >>> whole={"index":{"routing":{"allocation":{"include":{"group":"4,5"},"total_shards_per_node":"2"}},"refresh_interval":"60s","number_of_shards":"20","store":{"type":"niofs"},"number_of_replicas":"1"}}
    >>> part={"index":{"routing":{"allocation":{"include":{"group":"4,5"}}}}}
    >>> _compare_index_settings(part, whole)
    >>> part={"index":{"routing":{"allocation":{"include":{"group":"5"}}}}}
    >>> _compare_index_settings(part, whole)
    False
    '''
    if not isinstance(part, dict):
        return part == whole
    for k,v in part.items():
        if _compare_index_settings(v, whole.get(k)) is False:
            return False
    return True


def update_settings(esclient, indices, settings):
    """
    :type esclient: elasticsearch.Elasticsearch
    :type indices: list of (indexname,index_settings)
    :type settings: dict, index settings to be updated
    :rtype: None
    """
    if not indices:
        return
    logger.info("try to update index settings %s" %
                 ','.join([e[0] for e in indices]))
    dopey_summary.add(u"%s 更新索引配置" % ",".join([e[0] for e in indices]))
    index_client = elasticsearch.client.IndicesClient(esclient)
    global lock
    with lock:
        for index, index_settings in indices:
            origin_index_settings = index_client.get_settings(
                index=index)[index]['settings']
            logging.info('try to update settings for %s' % index)
            if _compare_index_settings(
                    settings.get('settings'),
                    origin_index_settings) is True:
                logging.info('unchanged settings, skip')
                continue
            index_client.put_settings(
                index=index,
                body=settings.get('settings', {}),
                params={'master_timeout': '300s'}
            )
            logging.info('finished to update settings for %s' % index)


# it NOT works, since some settings could not be upated
def revert_settings(esclient, indices, settings):
    """
    :type esclient: elasticsearch.Elasticsearch
    :type indices: list of (indexname,index_settings)
    :type settings: dict, not used
    :rtype: None
    """
    if not indices:
        return
    logger.debug("try to update index settings %s" %
                 ','.join([e[0] for e in indices]))
    dopey_summary.add(u"%s 恢复索引配置" % ",".join([e[0] for e in indices]))
    index_client = elasticsearch.client.IndicesClient(esclient)
    for index, index_settings in indices:
        index_client.put_settings(
            index=index,
            body=index_settings.get('settings', {}),
            params={'master_timeout': '300s'}
        )


def process(
    esclient,
    all_indices,
    index_prefix,
    index_config,
    base_day,
     action_filters):
    """
    :type esclient: elasticsearch.Elasticsearch
    :type all_indices: list of str
    :type index_prefix: str
    :type index_config: list of actions
    :rtype: list of indexname
    """
    index_client = elasticsearch.client.IndicesClient(esclient)
    actions = {}
    rst = []

    for indexname in all_indices:
        r = re.findall(
            r'^%s(\d{4}\.\d{2}\.\d{2})$' % index_prefix,
            indexname)
        if r:
            date = datetime.datetime.strptime(r[0], '%Y.%m.%d')
            rst.append(indexname)
        else:
            r = re.findall(
                r'^%s(\d{4}\.\d{2})$' % index_prefix,
                indexname)
            if r:
                date = datetime.datetime.strptime(r[0], '%Y.%m')
                rst.append(indexname)
            else:
                continue

        date = date.date()
        for e in index_config:
            action, settings = e.keys()[0], e.values()[0]
            offset = base_day-date
            if indexname in [e[0] for e in actions.get("delete_indices", [])]:
                continue
            if "day" in settings and offset == datetime.timedelta(
                    settings["day"]) or "days" in settings and offset >= datetime.timedelta(
                    settings["days"]):
                actions.setdefault(action, [])
                index_settings = index_client.get_settings(
                    index=indexname)
                actions[action].append((indexname, index_settings))

    # TODO 如果一个索引需要删除, 别的action里面可以直接去掉

    for e in index_config:
        action, settings = e.keys()[0], e.values()[0]
        logger.debug(action)
        if action not in action_filters:
            logger.info('skip %s' % action)
            continue
        logger.debug([e[0] for e in actions.get(action, [])])
        try:
            eval(action)(esclient, actions.get(action), settings)
        except Exception as e:
            logging.warn('%s action failed: %s' % (action, e))

    _dealt.extend(rst)
    return rst


def _get_base_day(base_day):
    try:
        int(base_day)
    except:
        return datetime.datetime.strptime(base_day, r'%Y-%m-%d').date()
    else:
        return (datetime.datetime.now() + datetime.timedelta(int(base_day))).date()


def _get_action_filters(action_filters_arg):
    action_filters_mapping = {
        'c': 'close_indices',
        'd': 'delete_indices',
        'u': 'update_settings',
        'f': 'optimize_indices',
    }
    if action_filters_arg == '':
        return action_filters_mapping.values()
    try:
        return [action_filters_mapping[k]
                for k in action_filters_arg.split(',')]
    except:
        raise Exception('unrecognizable action filters')


def main():
    global logger
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", default="dopey.yaml", help="yaml config file")
    parser.add_argument(
        "--base-day", default="0",
        help="number 0(today), 1(tommorow), -1(yestoday), or string line 2011-11-11")
    parser.add_argument(
        "--action-filters",
        default="",
     help="comma splited. d:delete, c:close, u:update settings, f:forcemerge. leaving blank means do all the actions configuared in config file")
    parser.add_argument(
        "-l",
        default="-",
        help="log file")
    parser.add_argument("--level", default="info")
    args = parser.parse_args()

    config = yaml.load(open(args.c))

    initlog(
        level=args.level, log=config["l"]
        if "log" in config else args.l)
    logger = logging.getLogger("dopey")

    eshosts = config.get("esclient")
    logger.debug(eshosts)
    if eshosts is not None:
        esclient = elasticsearch.Elasticsearch(eshosts, timeout=300)
    else:
        esclient = elasticsearch.Elasticsearch(timeout=300)

    all_indices = curator.get_indices(esclient)
    logger.debug("all_indices: {}".format(all_indices))
    if all_indices is False:
        raise Exception("could not get indices")

    for action in config.get('setup', []):
        settings = action.values()[0]
        eval(action.keys()[0])(esclient, settings)

    base_day = _get_base_day(args.base_day)
    logging.info('base day is %s' % base_day)
    action_filters = _get_action_filters(args.action_filters)

    process_threads = []
    for index_prefix, index_config in config.get("indices").items():
        t = Thread(
            target=process,
            args=(
                esclient,
                all_indices,
                index_prefix,
                index_config,
                base_day,
                action_filters,
            ))
        t.start()
        process_threads.append(t)

    for t in process_threads:
        t.join()

    not_dealt = list(set(all_indices).difference(_dealt))
    dopey_summary.add(
        json.dumps(
            {"not_dealt": not_dealt, "delete": _delete, "close": _close,
             "optimize": _optimize}, indent=2))
    sumary_config = config.get("sumary")
    for action, kargs in sumary_config.items():
        if kargs:
            getattr(dopey_summary, action)(**kargs)
        else:
            getattr(dopey_summary, action)()

    for action in config.get('teardown', []):
        settings = action.values()[0]
        eval(action.keys()[0])(esclient, settings)


if __name__ == '__main__':
    main()
