def validate_customer(customer):
    return customer.is_valid()


def validate_order(order):
    return order.is_complete()


def process_payment(payment, customer, season):
    if season == "holiday" and payment.is_authorized() and customer.has_credit():
        return handle_holiday_payment(payment, customer)

    if payment.is_authorized() and payment.method == "card":
        return handle_card_payment(payment)

    return handle_payment_error()
    

def evaluate_order(customer, order, payment, season):
    if validate_customer(customer) and validate_order(order):
        return process_payment(payment, customer, season)

    return handle_order_error()
