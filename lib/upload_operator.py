# Copyright © 2023 Roblox Corporation
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
# associated documentation files (the “Software”), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do
# so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial
# portions of the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS
# OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# SPDX-License-Identifier: MIT

import bpy
from bpy.types import Operator
import traceback
from tempfile import TemporaryDirectory
from pathlib import Path

NO_ASSET_ID = 0

# --------------------------------------------------------------------------------
# Module reload support (útil durante desenvolvimento dentro do Blender)
# --------------------------------------------------------------------------------
if "bpy" in locals():
    import importlib

    for module_name in [
        "get_selected_objects",
        "upload_blocking_issues",
        "creator_details",
        "status_indicators",
        "export_fbx",
        "get_add_on_preferences",
        "RbxOAuth2Client",
        "str_to_int",
        "constants",
        "AssetsUploadClient",
        "AssetsCreator",
        "AssetType",
        "openapi_client",
        "aiolimiter",
        "extract_exception_message",
        "event_loop",
    ]:
        if module_name in locals():
            importlib.reload(locals()[module_name])

# --------------------------------------------------------------------------------
# Classe principal: operador responsável por upload de objetos para o Roblox
# --------------------------------------------------------------------------------

class RBX_OT_upload(Operator):
    """Operator for uploading the current selection to Roblox"""

    bl_idname = "rbx.upload"
    bl_label = "Upload"
    limiter = None

    # -------------------------------------------------------------------------
    # Descrição e validação
    # -------------------------------------------------------------------------
    @classmethod
    def description(cls, context, _):
        from . import upload_blocking_issues
        from .get_selected_objects import get_selected_objects

        issues_blocking_upload = upload_blocking_issues.get_issues_blocking_upload(context)
        num_selected_objects = len(get_selected_objects(context))
        is_plural = num_selected_objects > 1
        default_string = f"Upload {num_selected_objects} selected object{'s' if is_plural else ''} to Roblox"
        error_string = ".\n".join(issues_blocking_upload)
        return error_string or default_string

    @classmethod
    def poll(cls, context):
        from . import upload_blocking_issues
        return upload_blocking_issues.get_can_upload(context)

    # -------------------------------------------------------------------------
    # Execução principal
    # -------------------------------------------------------------------------
    def execute(self, context):
        from . import upload_blocking_issues, status_indicators
        from .get_selected_objects import get_selected_objects

        if not upload_blocking_issues.get_can_upload(context):
            issues = upload_blocking_issues.get_issues_blocking_upload(context)
            self.report({"ERROR"}, ".\n".join(issues))
            return {"CANCELLED"}

        selected_objects = get_selected_objects(context)
        rbx = context.window_manager.rbx
        rbx.num_objects_uploading = len(selected_objects)

        status_indicators.clear_statuses(context.window_manager)

        for obj in selected_objects:
            self.upload(context.window_manager, context.area, context.scene,
                        context.view_layer, context.preferences, obj)

        return {"FINISHED"}

    # -------------------------------------------------------------------------
    # Exportação e início do upload
    # -------------------------------------------------------------------------
    @classmethod
    def upload(cls, window_manager, area, scene, view_layer, preferences, target_object):
        """Exports the given object to a FBX file, and uploads it to Roblox"""
        from . import status_indicators, constants

        try:
            from .get_add_on_preferences import get_add_on_preferences
            add_on_preferences = get_add_on_preferences(preferences)

            # Cria diretório temporário para exportação
            temporary_directory = TemporaryDirectory()

            # Garante que o nome do arquivo não contenha caracteres inválidos
            sanitized_object_name = "".join(
                c for c in target_object.name if c.isalnum() or c in (" ", ".", "_")
            ).rstrip()

            # Caminho final do arquivo FBX exportado
            exported_file_path = Path(temporary_directory.name) / f"exported_{sanitized_object_name}.fbx"

            # Exporta o objeto como FBX
            from .export_fbx import export_fbx
            export_fbx(scene, view_layer, target_object, exported_file_path, add_on_preferences)

        except Exception as exception:
            traceback.print_exception(exception)
            status_indicators.set_status(
                window_manager, area, target_object, constants.ERROR_MESSAGES["ADD_ON_ERROR"], "ERROR"
            )
            cls.upload_complete(window_manager, temporary_directory)
        else:
            status_indicators.set_status(window_manager, area, target_object, "Waiting to upload", "DECORATE")

            from .str_to_int import str_to_int
            package_id = str_to_int(target_object.get(constants.RBX_PACKAGE_ID_PROPERTY_NAME))

            # Cria coroutine de upload assíncrona
            coroutine = cls.upload_task(window_manager, area, target_object, exported_file_path, package_id)

            def task_complete(task):
                cls.upload_task_complete(task, window_manager, area, target_object, temporary_directory)

            from . import event_loop
            event_loop.submit(coroutine, task_complete)

    # -------------------------------------------------------------------------
    # Processo assíncrono de upload
    # -------------------------------------------------------------------------
    @classmethod
    async def upload_task(cls, window_manager, area, target_object, file_path, package_id):
        """Uploads the given FBX file to Roblox asynchronously"""
        from .oauth2_client import RbxOAuth2Client
        from . import creator_details, constants
        from .assets_upload_client import AssetsUploadClient  # <- corrigido import
        from openapi_client.models import (
            RobloxOpenCloudAssetsV1Creator as AssetsCreator,
            RobloxOpenCloudAssetsV1AssetType as AssetType,
        )

        creator_data = creator_details.get_selected_creator_data(window_manager)
        rbx = window_manager.rbx

        oauth2_client = RbxOAuth2Client(rbx)
        await oauth2_client.refresh_login_if_needed()
        access_token = oauth2_client.token_data["access_token"]

        # Define o criador (usuário ou grupo)
        match creator_data.type:
            case "USER":
                creator = AssetsCreator(user_id=int(creator_data.id))
            case "GROUP":
                creator = AssetsCreator(group_id=int(creator_data.id))

        # Limita o número de uploads por minuto
        if not cls.limiter:
            import aiolimiter
            cls.limiter = aiolimiter.AsyncLimiter(constants.MAX_UPLOADS_PER_MIN)

        async with cls.limiter, AssetsUploadClient(creator=creator, oauth2_token=access_token) as client:
            from . import status_indicators

            status_indicators.set_status(window_manager, area, target_object, "Uploading", "DECORATE")

            operation = await client.upload_asset_and_wait_for_done_async(
                asset_type=AssetType.MODEL,
                asset_name=target_object.name,
                asset_description=constants.ASSET_DESCRIPTION,
                file_path=file_path,
                asset_id=package_id or NO_ASSET_ID,
                upload_request_timeout_seconds=25,
            )

        return operation

    # -------------------------------------------------------------------------
    # Finalização e tratamento de resultados
    # -------------------------------------------------------------------------
    @staticmethod
    def upload_complete(window_manager, temporary_directory):
        """Decrementa o contador quando o upload termina (sucesso ou erro)"""
        temporary_directory.cleanup()
        rbx = window_manager.rbx
        rbx.num_objects_uploading -= 1

    @staticmethod
    def upload_task_complete(task, window_manager, area, target_object, temporary_directory):
        """Handles the result of an upload task and updates UI status"""
        from . import status_indicators, constants
        import openapi_client
        import asyncio

        try:
            operation = task.result()
            print(f"Operation path: {operation.path}")

            if operation.error:
                status_indicators.set_status(window_manager, area, target_object, operation.error.message, "ERROR")
                print(f"Upload failed, {operation.error.code}: {operation.error.message}")

            elif not operation.done:
                status_indicators.set_status(
                    window_manager, area, target_object, constants.ERROR_MESSAGES["OPERATION_TIMED_OUT"], "ERROR"
                )

            elif operation.response:
                target_object[constants.RBX_PACKAGE_ID_PROPERTY_NAME] = str(operation.response.asset_id)
                status_indicators.set_status(
                    window_manager, area, target_object,
                    f"Uploaded version {operation.response.revision_id}", "CHECKMARK"
                )

            else:
                status_indicators.set_status(
                    window_manager, area, target_object, constants.ERROR_MESSAGES["INVALID_RESPONSE"], "ERROR"
                )
                print(f"Upload failed, invalid response:\n{operation}")

        except asyncio.exceptions.TimeoutError:
            status_indicators.set_status(
                window_manager, area, target_object, constants.ERROR_MESSAGES["UPLOAD_TIMED_OUT"], "ERROR"
            )

        except openapi_client.rest.ApiException as exception:
            traceback.print_exception(exception)
            from .extract_exception_message import extract_exception_message
            status_indicators.set_status(
                window_manager, area, target_object,
                extract_exception_message(exception), "ERROR"
            )

        except Exception as exception:
            traceback.print_exception(exception)
            status_indicators.set_status(
                window_manager, area, target_object, constants.ERROR_MESSAGES["ADD_ON_ERROR"], "ERROR"
            )

        finally:
            RBX_OT_upload.upload_complete(window_manager, temporary_directory)
