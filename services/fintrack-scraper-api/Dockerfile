FROM public.ecr.aws/lambda/python:3.13

COPY fintrack-scraper-api/requirements.txt ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY fintrack-scraper-api/scraper.py /var/task/
COPY fintrack-scraper-api/scraper_api_handler.py /var/task/
COPY utils /var/task/utils

ENTRYPOINT ["/lambda-entrypoint.sh"]
CMD ["scraper_api_handler.lambda_handler"]
