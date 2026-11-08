from curses import meta

from rest_framework.response import Response


class APIResponse:
    @staticmethod
    def success(data=None, meta=None, message="Success", status=200):

        response = {"status": status, "success": True, "message": message}
        
        if meta is not None:
            response["meta"] = meta

        if data is not None:
            response["data"] = data
        
        return Response(response, status=status)

    @staticmethod
    def error(errors=None, message="An error occured!", status=400, meta=None):

        response = {"status": status, "success": False, "message": message}

        if errors is not None:
            response["errors"] = errors

        if meta is not None:
            response["meta"] = meta

        return Response(response, status=status)
