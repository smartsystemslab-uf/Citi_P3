from dotenv import load_dotenv

load_dotenv()

from bank_adk.infra.api_client import api_client, json_dumps


def main():
    print("BANKING BACKEND HEALTH")
    print(json_dumps(api_client.health()))

    print("\nCUSTOMER CONTEXT: CUST-001")
    print(json_dumps(api_client.get_customer_context("CUST-001")))

    print("\nPOLICY SEARCH: AML")
    print(json_dumps(api_client.search_policies("AML large transfer")))

    print("\nEVALUATE LOW-RISK TRANSFER")
    print(json_dumps(api_client.evaluate_transaction("CHK-002", "EXT-998", 125.50)))

    print("\nEVALUATE HIGH-RISK TRANSFER")
    print(json_dumps(api_client.evaluate_transaction("CHK-9001", "EXT-998", 60000.00)))


if __name__ == "__main__":
    main()
