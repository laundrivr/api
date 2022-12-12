from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from asgiref.sync import async_to_sync
import json
from square.client import Client as SquareClient
from supabase import create_client, Client as SupabaseClient
import os
import logging
from django.conf import settings

fmt = getattr(settings, "LOG_FORMAT", None)
lvl = getattr(settings, "LOG_LEVEL", logging.ERROR)

logging.basicConfig(format=fmt, level=lvl)

# get the square access token from the environment
SQUARE_ACCESS_TOKEN = os.environ.get("SQUARE_ACCESS_TOKEN")
# get the square environment from the environment
SQUARE_ENVIRONMENT = os.environ.get("SQUARE_ENVIRONMENT")

# get the supabase url from the environment
SUPABASE_URL = os.environ.get("SUPABASE_URL")
# get the supabase key from the environment
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

# initialize the square client
square_client: SquareClient = SquareClient(
    access_token=SQUARE_ACCESS_TOKEN,
    environment=SQUARE_ENVIRONMENT,
)

# initialize the supabase client
supabase_client: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def index(request) -> HttpResponse:
    return HttpResponse("Access denied.")


@csrf_exempt
@async_to_sync
async def payment(request: any) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("Access denied.")

    # get the payload from the request
    payload = request.body
    # decode the payload
    payload = payload.decode("utf-8")
    # convert the payload to a dictionary
    payload = json.loads(payload)

    # if the payload is empty, return an error
    if not payload:
        return HttpResponse("Payload is empty.", status=500)

    # if the payload doesn't contain the data key, return an error
    if (
        "data" not in payload
        or "object" not in payload["data"]
        or "payment" not in payload["data"]["object"]
    ):
        return HttpResponse("Payload is invalid.", status=500)

    # get the order id from the payload
    order_id = payload["data"]["object"]["payment"]["order_id"]

    # get the order from square
    result = square_client.orders.retrieve_order(order_id)

    # if the order had a problem, return an error
    if result.is_error():
        return HttpResponse("Order not found: " + str(result.errors), status=500)

    # get the order from the response
    order: dict = result.body["order"]

    # we can't get the customer id from the order because
    # it changes when the payment happens (instant profiles)
    # https://developer.squareup.com/docs/customers-api/what-it-does#instant-profiles

    # so let's grab the customer id from the supabase database
    # using the order id
    customer_id: str = None
    try:
        logging.debug(
            "Getting customer id from the pending transactions database for order id "
            + order_id
        )
        print(
            "Getting customer id from the pending transactions database for order id "
            + order_id
        )
        customer_response = (
            supabase_client.table("pending_transactions")
            .select("original_square_customer_id")
            .eq("square_order_id", order_id)
            .limit(1)
            .execute()
        )

        if len(customer_response.data) <= 0:
            logging.error(
                "Error getting customer id from the pending transactions database: No customer id found for order id "
                + order_id
            )
            print(
                "Error getting customer id from the pending transactions database: No customer id found for order id "
                + order_id
            )
            return HttpResponse(
                "Error getting customer id from the pending transactions database: No customer id found for order id "
                + order_id,
                status=500,
            )

        logging.debug("Customer response: " + str(customer_response))
        print("Customer response: " + str(customer_response))

        customer_id = customer_response.data[0]["original_square_customer_id"]
        logging.debug(
            "Fetched customer id from the pending transactions database: "
            + str(customer_id)
        )
        print(
            "Fetched customer id from the pending transactions database: "
            + str(customer_id)
        )
    except Exception as e:
        logging.error(
            "Error getting customer id from the pending transactions database: "
            + str(e)
        )
        print(
            "Error getting customer id from the pending transactions database: "
            + str(e)
        )
        return HttpResponse(
            "Error getting customer id from the pending transactions database: "
            + str(e),
            status=500,
        )

    # get the id of the first line item in the order (the variation id)
    package_id: str = order["line_items"][0]["catalog_object_id"]

    response = None

    logging.debug(
        "Calling supabase function to process payment for customer id "
        + str(customer_id)
        + " and package id "
        + str(package_id)
    )
    print(
        "Calling supabase function to process payment for customer id "
        + str(customer_id)
        + " and package id "
        + str(package_id)
    )

    try:
        # call the supabase function to process the payment
        response = await supabase_client.functions().invoke(
            "square-payment-callback",
            invoke_options={
                "body": {"customer_id": customer_id, "package_id": package_id}
            },
        )
    except Exception as e:
        return HttpResponse("Error processing payment: " + str(e), status=500)

    # if the function call had a problem, return an error
    if "error" in response and response["error"] is not None:
        return HttpResponse(
            "Error processing payment: " + str(response["error"]), status=500
        )

    # return a success message
    return HttpResponse("Payment processed successfully.", status=200)
