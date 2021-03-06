# -*- encoding: utf-8 -*
import random
import requests
from pyquery import PyQuery as pq
import time
from logger import Logger
from worker import *
from article import *
from mongoengine import *

logger = Logger(logname='qq.log', logger=__name__).get_logger()


class QqWorker(Worker):
    def __init__(self, startdate=date(2009, 1, 1), enddate=date(2015, 6, 1)):
        super(QqWorker, self).__init__()
        self.beginDate = startdate
        self.endDate = enddate

    # http://roll.news.qq.com/interface/roll.php?0.3343920919516311&cata=&site=news&date=2009-01-01&page=1&mode=2&of=json
    #           国内 newsgn 国际 newsgj 社会 newssh
    __listUrl = 'http://roll.news.qq.com/interface/roll.php?{0}&cata={1}&site=news&date={2}&page={3}&mode=2&of=json'
    __referUrl = 'http://roll.news.qq.com/index.htm?site=news&mod=2&date={0}&cata='

    def start(self):
        self.get_records()
        day = self.beginDate
        while day <= self.endDate:
            date_str = day.strftime("%Y-%m-%d")
            if date_str not in self.history:
                self.crawl_by_day(date_str)
            day += Worker.dayDelta
        self.reget_errorlist()

    def get_records(self):
        self.history = Article.objects.distinct('post_date')

    def crawl_by_day(self, date_str):
        try:
            for catg in ['newsgn', 'newsgj', 'newssh']:
                page = 1
                random_str = random.random()
                headers = {'Referer': self.__referUrl.format(date_str)}
                # 当日首页
                res = requests.get(self.__listUrl.format(random_str, catg, date_str, page), headers=headers,
                                   timeout=self.timeout)
                jo = res.json()
                responsecode = jo['response']['code']
                if responsecode == '0':
                    pagecount = jo['data']['count']
                    articles = jo['data']['article_info']
                    self.parse_articles_list(articles, date_str)
                    # 循环分页
                    while page < pagecount:
                        page += 1
                        res = requests.get(self.__listUrl.format(random_str, catg, date_str, page), headers=headers,
                                           timeout=self.timeout)
                        jo = res.json()
                        articles = jo['data']['article_info']
                        self.parse_articles_list(articles, date_str)
            self.save_temp_dict()
        except Exception, e:
            logger.error(date_str + ' error',  e.message)
        finally:
            self.newsDict.clear()

    def parse_articles_list(self, articles, post_date):
        doc = pq(articles)
        for i in range(1, doc('div').length):
            div = doc('div').eq(i)
            title = div('dt a').text()
            category = div('.t-tit').text().strip('[]')
            itemstr = div('.t-time').text()
            post_time = itemstr.split(' ')[1]
            link = div('dt a').attr('href')
            # 丢掉阅读全文几个字
            div('dl dd a').empty()
            summary = div('dl dd').text()
            item = {'title': title, 'summary': summary, 'link': link, 'category': category, 'post_date': post_date,
                    'post_time': post_time, 'valid': True, 'error_count': 0}
            self.newsDict[link] = item
            # 重试
            retry = 1
            while not self.get_detail(link):
                if retry <= 0:
                    self.newsDict[link]['valid'] = False
                    break
                else:
                    retry -= 1
                    time.sleep(1)

    def get_detail(self, url):
        logger.info(url)
        try:
            r = requests.get(url, timeout=self.timeout)
            r.raise_for_status()
            d = pq(r.text)
            self.newsDict[url]['source'] = ''
            self.newsDict[url]['source_link'] = ''
            cmt_id = get_content_between(d.html(), 'cmt_id = ', ';')
            d('script').remove()
            d('#backqqcom').remove()
            if d('#ArticleCnt'):
                if len(d('#ArtFrom a')) > 2:
                    self.newsDict[url]['source'] = d('#ArtFrom a').eq(1).text()
                    self.newsDict[url]['source_link'] = d('#ArtFrom a').eq(1).attr('href')
                self.newsDict[url]['content'] = d('#ArticleCnt p').text()
                self.newsDict[url]['image_links'] = [i.attr('src') for i in d.items('#ArticleCnt img')]
                self.newsDict[url]['video_links'] = ['http://static.video.qq.com/TPout.swf?' + i.attr('flashvars') for i
                                                     in
                                                     d.items('object embed')]
                # 文章内有分页的情况 http://news.qq.com/a/20090401/001492.htm
                if d('#ArticlePageLinkB'):
                    pgs = len(d('#ArticlePageLinkB a'))
                    for p in range(1, pgs):
                        innerurl = url.replace('.htm', '_' + str(p) + '.htm')
                        rr = requests.get(innerurl, timeout=self.timeout)
                        # 跳过有问题的分页 http://news.qq.com/a/20110107/000908_2.htm
                        try:
                            dd = pq(rr.text)
                            if dd('#ArticleCnt'):
                                self.newsDict[url]['content'] += dd('#ArticleCnt p').text()
                                self.newsDict[url]['image_links'] += [i.attr('src') for i in
                                                                      dd.items('#ArticleCnt img')]
                                self.newsDict[url]['video_links'] += [
                                    'http://static.video.qq.com/TPout.swf?' + i.attr('flashvars') for i in
                                    dd.items('object embed')]
                        except Exception:
                            pass
            elif d('#C-Main-Article-QQ'):
                source = d('#C-Main-Article-QQ .tit-bar .color-a-1').text()
                if len(source) == 0:
                    source = d('#C-Main-Article-QQ .infoCol .where').text()
                self.newsDict[url]['source'] = source
                if d('#C-Main-Article-QQ .tit-bar .color-a-1 a'):
                    source_link = d('#C-Main-Article-QQ .tit-bar .color-a-1 a').attr('href')
                elif d('#C-Main-Article-QQ .infoCol .where a'):
                    source_link = d('#C-Main-Article-QQ .infoCol .where a').attr('href')
                else:
                    source_link = ''
                self.newsDict[url]['source_link'] = source_link
                self.newsDict[url]['content'] = d('#Cnt-Main-Article-QQ p').text()
                self.newsDict[url]['image_links'] = [i.attr('src') for i in d.items('#Cnt-Main-Article-QQ p img')]
                self.newsDict[url]['video_links'] = ['http://static.video.qq.com/TPout.swf?' + i.attr('flashvars') for i
                                                     in
                                                     d.items('#C-Main-Article-QQ embed')]
                # 文章内有分页的情况 http://news.qq.com/a/20110406/001540.htm
                if d('#ArtPLink'):
                    pgs = len(d('#ArtPLink a'))
                    for p in range(1, pgs):
                        innerurl = url.replace('.htm', '_' + str(p) + '.htm')
                        rr = requests.get(innerurl, timeout=self.timeout)
                        try:
                            dd = pq(rr.text)
                            if dd('#C-Main-Article-QQ'):
                                self.newsDict[url]['content'] += dd('#Cnt-Main-Article-QQ p').text()
                                self.newsDict[url]['image_links'] += [i.attr('src') for i in
                                                                      dd.items('#Cnt-Main-Article-QQ p img')]
                                self.newsDict[url]['video_links'] += [
                                    'http://static.video.qq.com/TPout.swf?' + i.attr('flashvars') for i in
                                    dd.items('#C-Main-Article-QQ embed')]
                        except Exception:
                            pass
            elif d('#qnews-content'):
                self.newsDict[url]['source'] = d('#qnews-content .tomobile').text()
                if d('#qnews-content .tomobile a').attr.href:
                    self.newsDict[url]['source_link'] = d('#qnews-content .tomobile a').attr.href
                self.newsDict[url]['content'] = d('#qnews-content p').text()
                self.newsDict[url]['image_links'] = [i.attr('src') for i in d.items('#qnews-content img')]
                self.newsDict[url]['video_links'] = ['http://static.video.qq.com/TPout.swf?' + i.attr('flashvars') for i
                                                     in
                                                     d.items('#qnews-content embed')]
            else:
                self.newsDict[url]['valid'] = False
                logger.error('content null:' + url)
                return True
            if cmt_id and cmt_id != '':
                if int(cmt_id) < 100000000:
                    nums = self.get_comment_num(cmt_id)
                else:
                    nums = self.get_comment_num_new(cmt_id)
                self.newsDict[url]['comment_num'] = nums[0]
                self.newsDict[url]['reply_num'] = nums[1]
            else:
                self.newsDict[url]['comment_num'] = '0'
                self.newsDict[url]['reply_num'] = '0'
            return True
        except Exception, e:
            logger.error('get detail error:' + url + e.message)
            return False

    __commentNumUrl = 'http://sum.comment.gtimg.com.cn/php_qqcom/gsum.php?site=news&c_id={0}'

    def get_comment_num(self, cmt_id):
        r = requests.get(self.__commentNumUrl.format(cmt_id), timeout=self.timeout)
        if r.text.startswith('_cbSum'):
            cont = get_content_between(r.text, '(', ')')
            nums = cont.split(',')
            if len(nums) > 1:
                return nums[0], nums[1]
            else:
                return nums[0], '0'
        else:
            return '0', '0'

    __commentNumUrlNew = 'http://coral.qq.com/article/{0}/commentnum'

    # 新版没有参与数
    def get_comment_num_new(self, cmt_id):
        headers = {'User-Agent': 'Chrome/44.0.2403.39 Safari/537.36'}
        r = requests.get(self.__commentNumUrlNew.format(cmt_id), headers=headers, timeout=self.timeout)
        jo = r.json()
        if jo['errCode'] == 0:
            return str(jo['data']['commentnum']), '0'
        else:
            return '0' '0'
