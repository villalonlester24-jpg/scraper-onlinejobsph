import scrapy
import os
import re
import json
import datetime



class JobsSpider(scrapy.Spider):
    name = "jobs"

    base_url = "https://www.onlinejobs.ph"
    search_page = "/jobseekers/jobsearch"


    # No keyword filter: every post on the search page is scraped.


    # Element Selectors
    list_posts = "div.jobpost-cat-box"
    post_date = "p em::text"
    post_role = "h4::text"
    post_client = "p::text"
    post_salary = "dl.no-gutters dd::text"
    post_url = "a"
    job_description = "#job-description"




    # Method to Clean Scraped Text
    def clean(self, text):
        return " ".join(text.split())





    def __init__(self, offset='', cache='', **kwargs):

        self.start_urls = []
        self.offset = int(offset)

        # URL -> description we already scraped. Reuse it instead of
        # re-fetching the job page on every run.
        self.cache = {}
        if cache and os.path.exists(cache):
            with open(cache, encoding='utf-8') as fh:
                self.cache = json.load(fh)

        if self.offset < 2:
            start = 0
        else:
            start = (self.offset * 25)
        end = start + 25

        # Generate Multiple Pages to Scrape
        for i in range(start, end):
            self.start_urls.append(''.join([self.base_url, self.search_page, '/', str(i * 30)]))
        super().__init__(**kwargs)



    def parse(self, response):
        today = datetime.date.today()
        period = today - datetime.timedelta(self.offset)

        jobs = response.css(self.list_posts)


        for job in jobs:
            # Only Scrape Posts that are for Today or Yesterday
            date = job.css(self.post_date).get(default='')
            match = re.search(r'(\d{4})-(\d{2})-(\d{2})', self.clean(date))
            post_date = datetime.date(*map(int, match.groups())) if match else None

            if post_date == period:

                url = self.base_url + job.css(self.post_url)[0].attrib['href']
                role = self.clean(job.css(self.post_role).get(default=''))
                client = self.clean(job.css(self.post_client).get(default=''))
                salary = self.clean(job.css(self.post_salary).get(default=''))

                # Reuse the description already scraped for this URL
                cached_desc = self.cache.get(url)
                if cached_desc:
                    yield {
                        'role': role,
                        'client': client,
                        'salary': salary,
                        'desc': cached_desc,
                        'date': date,
                        'url': url,
                    }
                    continue

                # Otherwise follow the job page to grab the full description
                yield scrapy.Request(
                    url,
                    callback=self.parse_job,
                    cb_kwargs={
                        'role': role,
                        'client': client,
                        'salary': salary,
                        'date': date,
                        'url': url,
                    },
                )



    def parse_job(self, response, role, client, salary, date, url):
        # Full description lives on the job page, not the search listing
        desc = response.css(self.job_description).xpath("string(.)").get(default='')

        yield {
            'role': role,
            'client': client,
            'salary': salary,
            'desc': self.clean(desc),
            'date': date,
            'url': url,
        }




# To run:
# scrapy crawl jobs -o jobs.json
