FROM public.ecr.aws/lambda/python:3.13

# Install packages
COPY fintrack-upload-api/requirements.txt ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy function code
COPY fintrack-upload-api/lambda_function.py /var/task/
COPY utils /var/task/utils

# Set the CMD to your handler (could also be done as a parameter override outside of the Dockerfile)
ENTRYPOINT ["/lambda-entrypoint.sh"]
CMD [ "lambda_function.lambda_handler" ]