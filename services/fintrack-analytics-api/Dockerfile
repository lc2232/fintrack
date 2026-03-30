FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy function code
COPY lambda_function.py /var/task/

# Set the CMD to your handler (could also be done as a parameter override outside of the Dockerfile)
ENTRYPOINT ["/lambda-entrypoint.sh"]
CMD [ "lambda_function.lambda_handler" ]