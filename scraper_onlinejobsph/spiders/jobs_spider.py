import scrapy
import re
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
    post_desc = "div.desc::text"
    post_url = "a"




    # Method to Clean Scraped Text
    def clean(self, text):
        text = text.encode("ascii", "ignore")
        text = text.decode()
        return text.replace("\n",'').replace("\r",' ').strip()





    def __init__(self, offset='', **kwargs):

        self.start_urls = []
        self.offset = int(offset)

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

                # Scrape Desired Information
                role = job.css(self.post_role).get(default='')
                client = job.css(self.post_client).get(default='')
                salary = job.css(self.post_salary).get(default='')
                desc = job.css(self.post_desc).get(default='')
                url = self.base_url + job.css(self.post_url)[0].attrib['href']

                yield {
                    'role': self.clean(role),
                    'client': self.clean(client),
                    'salary': self.clean(salary),
                    'desc': self.clean(desc),
                    'date': date,
                    'url': url,
                }




# To run:
# scrapy crawl jobs -o jobs.json