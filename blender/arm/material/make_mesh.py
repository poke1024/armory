import bpy
import arm.assets as assets
import arm.material.mat_state as mat_state
import arm.material.mat_utils as mat_utils
import arm.material.cycles as cycles
import arm.material.make_skin as make_skin
import arm.material.make_tess as make_tess
import arm.material.make_particle as make_particle
import arm.utils

is_displacement = False
write_material_attribs = None
write_material_attribs_post = None
write_vertex_attribs = None

def make(context_id):
    rpdat = arm.utils.get_rp()
    rid = rpdat.rp_renderer

    con = { 'name': context_id, 'depth_write': True, 'compare_mode': 'less', 'cull_mode': 'clockwise' }
    
    # TODO: blend context
    blend = mat_state.material.arm_blending
    if blend:
        con['name'] = 'blend'
        con['blend_source'] = 'blend_one'
        con['blend_destination'] = 'blend_one'
        con['blend_operation'] = 'add'
        con['depth_write'] = False
    dprepass = rid == 'Forward' and rpdat.rp_depthprepass
    if dprepass:
        con['depth_write'] = False
        con['compare_mode'] = 'equal'

    con_mesh = mat_state.data.add_context(con)
    mat_state.con_mesh = con_mesh

    if rid == 'Forward' or blend:
        if rpdat.arm_material_model == 'Mobile':
            make_forward_mobile(con_mesh)
        elif rpdat.arm_material_model == 'Solid':
            make_forward_solid(con_mesh)
        else:
            make_forward(con_mesh)
    elif rid == 'Deferred':
        if rpdat.arm_material_model != 'Full': # TODO: hide material enum
            print('Armory Warning: Deferred renderer only supports Full materials')
        make_deferred(con_mesh)
    elif rid == 'Deferred Plus':
        make_deferred_plus(con_mesh)

    make_finalize(con_mesh)

    assets.vs_equal(con_mesh, assets.shader_cons['mesh_vert'])

    return con_mesh

def make_finalize(con_mesh):
    vert = con_mesh.vert
    frag = con_mesh.frag
    geom = con_mesh.geom
    tesc = con_mesh.tesc
    tese = con_mesh.tese

    # Additional values referenced in cycles
    # TODO: enable from cycles.py
    if frag.contains('dotNV') and not frag.contains('float dotNV'):
        frag.write_init('float dotNV = max(dot(n, vVec), 0.0);')
    
    write_wpos = False
    if frag.contains('vVec') and not frag.contains('vec3 vVec'):
        if tese != None:
            tese.add_out('vec3 eyeDir')
            tese.add_uniform('vec3 eye', '_cameraPosition')
            tese.write('eyeDir = eye - wposition;')

        else:
            if not vert.contains('wposition'):
                write_wpos = True
            vert.add_out('vec3 eyeDir')
            vert.add_uniform('vec3 eye', '_cameraPosition')
            vert.write('eyeDir = eye - wposition;')
        frag.write_attrib('vec3 vVec = normalize(eyeDir);')
    
    export_wpos = False
    if frag.contains('wposition') and not frag.contains('vec3 wposition'):
        export_wpos = True
    if tese != None:
        export_wpos = True
    if vert.contains('wposition'):
        write_wpos = True
    
    if export_wpos:
        vert.add_uniform('mat4 W', '_worldMatrix')
        vert.add_out('vec3 wposition')
        vert.write_attrib('wposition = vec4(W * spos).xyz;')
    elif write_wpos:
        vert.add_uniform('mat4 W', '_worldMatrix')
        vert.write_attrib('vec3 wposition = vec4(W * spos).xyz;')

    frag_mpos = frag.contains('mposition') and not frag.contains('vec3 mposition') or vert.contains('mposition')
    if frag_mpos:
        vert.add_out('vec3 mposition')
        vert.write_attrib('mposition = spos.xyz;')
    
    if tese != None:
        if frag_mpos:
            make_tess.interpolate(tese, 'mposition', 3, declare_out=True)
        elif tese.contains('mposition') and not tese.contains('vec3 mposition'):
            vert.add_out('vec3 mposition')
            vert.write_pre = True
            vert.write('mposition = spos.xyz;')
            vert.write_pre = False
            make_tess.interpolate(tese, 'mposition', 3, declare_out=False)

    frag_bpos = frag.contains('bposition') and not frag.contains('vec3 bposition') or vert.contains('bposition')
    if frag_bpos:
        vert.add_out('vec3 bposition')
        vert.add_uniform('vec3 dim', link='_dim')
        vert.add_uniform('vec3 hdim', link='_halfDim')
        vert.write_pre = True
        vert.write('bposition = (spos.xyz + hdim) / dim;')
        vert.write_pre = False
    
    if tese != None:
        if frag_bpos:
            make_tess.interpolate(tese, 'bposition', 3, declare_out=True)
        elif tese.contains('bposition') and not tese.contains('vec3 bposition'):
            vert.add_out('vec3 bposition')
            vert.write_pre = True
            vert.write('bposition = spos.xyz;')
            vert.write_pre = False
            make_tess.interpolate(tese, 'bposition', 3, declare_out=False)

def make_base(con_mesh, parse_opacity):
    global is_displacement
    global write_material_attribs
    global write_material_attribs_post
    global write_vertex_attribs

    vert = con_mesh.make_vert()
    frag = con_mesh.make_frag()
    geom = None
    tesc = None
    tese = None

    vert.add_uniform('mat3 N', '_normalMatrix')
    vert.write_attrib('    vec4 spos = vec4(pos, 1.0);')

    vattr_written = False
    rpdat = arm.utils.get_rp()
    is_displacement = mat_utils.disp_linked(mat_state.output_node)
    if is_displacement:
        if rpdat.arm_rp_displacement == 'Vertex':
            frag.ins = vert.outs
        else: # Tessellation
            tesc = con_mesh.make_tesc()
            tese = con_mesh.make_tese()
            tesc.ins = vert.outs
            tese.ins = tesc.outs
            frag.ins = tese.outs
            make_tess.tesc_levels(tesc, rpdat.arm_tess_mesh_inner, rpdat.arm_tess_mesh_outer)
            make_tess.interpolate(tese, 'wposition', 3, declare_out=True)
            make_tess.interpolate(tese, 'wnormal', 3, declare_out=True, normalize=True)
    # No displacement
    else:
        frag.ins = vert.outs
        if write_vertex_attribs != None:
            vattr_written = write_vertex_attribs(vert)

    frag.add_include('compiled.glsl')

    written = False
    if write_material_attribs != None:
        written = write_material_attribs(con_mesh, frag)
    if written == False:
        frag.write('vec3 basecol;')
        frag.write('float roughness;')
        frag.write('float metallic;')
        frag.write('float occlusion;')
        frag.write('float specular;')
        if parse_opacity:
            frag.write('float opacity;')
        cycles.parse(mat_state.nodes, con_mesh, vert, frag, geom, tesc, tese, parse_opacity=parse_opacity)
    if write_material_attribs_post != None:
        write_material_attribs_post(con_mesh, frag)

    if not is_displacement and not vattr_written:
        write_vertpos(vert)

    if con_mesh.is_elem('tex'):
        vert.add_out('vec2 texCoord')
        if mat_state.material.arm_tilesheet_mat:
            if mat_state.material.arm_particle == 'gpu':
                make_particle.write_tilesheet(vert)
            else:
                vert.add_uniform('vec2 tilesheetOffset', '_tilesheetOffset')
                vert.write_attrib('texCoord = tex + tilesheetOffset;')
        else:
            vert.write_attrib('texCoord = tex;')

        if tese != None:
            tese.write_pre = True
            make_tess.interpolate(tese, 'texCoord', 2, declare_out=frag.contains('texCoord'))
            tese.write_pre = False

    if con_mesh.is_elem('tex1'):
        vert.add_out('vec2 texCoord1')
        vert.write_attrib('texCoord1 = tex1;')
        if tese != None:
            tese.write_pre = True
            make_tess.interpolate(tese, 'texCoord1', 2, declare_out=frag.contains('texCoord1'))
            tese.write_pre = False

    if con_mesh.is_elem('col'):
        vert.add_out('vec3 vcolor')
        vert.write_attrib('vcolor = col;')
        if tese != None:
            tese.write_pre = True
            make_tess.interpolate(tese, 'vcolor', 3, declare_out=frag.contains('vcolor'))
            tese.write_pre = False

    if con_mesh.is_elem('tang'):
        if tese != None:
            vert.add_out('vec3 wnormal')
            vert.add_out('vec3 wtangent')
            write_norpos(con_mesh, vert)
            vert.write('wtangent = normalize(N * tang);')
            tese.add_out('mat3 TBN')
            make_tess.interpolate(tese, 'wtangent', 3, normalize=True)
            tese.write('vec3 wbitangent = normalize(cross(wnormal, wtangent));')
            tese.write('TBN = mat3(wtangent, wbitangent, wnormal);')
        else:
            vert.add_out('mat3 TBN')
            write_norpos(con_mesh, vert, declare=True)
            vert.write('vec3 tangent = normalize(N * tang);')
            vert.write('vec3 bitangent = normalize(cross(wnormal, tangent));')
            vert.write('TBN = mat3(tangent, bitangent, wnormal);')
    else:
        vert.add_out('vec3 wnormal')
        write_norpos(con_mesh, vert)
        frag.write_attrib('vec3 n = normalize(wnormal);')

    if is_displacement:
        if rpdat.arm_rp_displacement == 'Vertex':
            sh = vert
        else:
            sh = tese
        sh.add_uniform('mat4 VP', '_viewProjectionMatrix')
        sh.write('wposition += wnormal * disp * 0.1;')
        sh.write('gl_Position = VP * vec4(wposition, 1.0);')

def write_vertpos(vert):
    billboard = mat_state.material.arm_billboard
    particle = mat_state.material.arm_particle
    # Particles
    if particle != 'off':
        if particle == 'gpu':
            make_particle.write(vert, particle_info=cycles.particle_info)
        # Billboards
        if billboard == 'spherical':
            vert.add_uniform('mat4 WV', '_worldViewMatrix')
            vert.add_uniform('mat4 P', '_projectionMatrix')
            vert.write('gl_Position = P * (WV * vec4(0.0, 0.0, spos.z, 1.0) + vec4(spos.x, spos.y, 0.0, 0.0));')
        else:
            vert.add_uniform('mat4 WVP', '_worldViewProjectionMatrix')
            vert.write('gl_Position = WVP * spos;')
    else:
        # Billboards
        if billboard == 'spherical':
            vert.add_uniform('mat4 WVP', '_worldViewProjectionMatrixSphere')
        elif billboard == 'cylindrical':
            vert.add_uniform('mat4 WVP', '_worldViewProjectionMatrixCylinder')
        else: # off
            vert.add_uniform('mat4 WVP', '_worldViewProjectionMatrix')
        vert.write('gl_Position = WVP * spos;')

def write_norpos(con_mesh, vert, declare=False, write_nor=True):
    prep = ''
    if declare:
        prep = 'vec3 '
    vert.write_pre = True
    is_bone = con_mesh.is_elem('bone')
    if is_bone:
        make_skin.skin_pos(vert)
    if write_nor:
        if is_bone:
            make_skin.skin_nor(vert, prep)
        else:
            vert.write(prep + 'wnormal = normalize(N * nor);')
    if con_mesh.is_elem('off'):
        vert.write('spos.xyz += off;')
    vert.write_pre = False

def make_deferred(con_mesh):
    wrd = bpy.data.worlds['Arm']
    rpdat = arm.utils.get_rp()

    arm_discard = mat_state.material.arm_discard
    parse_opacity = arm_discard or rpdat.arm_voxelgi_refraction

    make_base(con_mesh, parse_opacity=parse_opacity)

    frag = con_mesh.frag
    vert = con_mesh.vert
    tese = con_mesh.tese

    if arm_discard:
        opac = mat_state.material.arm_discard_opacity
        frag.write('if (opacity < {0}) discard;'.format(opac))

    gapi = arm.utils.get_gapi()
    if '_gbuffer2' in wrd.world_defs:
        frag.add_out('vec4[3] fragColor')
        if '_Veloc' in wrd.world_defs:
            if tese == None:
                vert.add_uniform('mat4 prevWVP', link='_prevWorldViewProjectionMatrix')
                vert.add_out('vec4 wvpposition')
                vert.add_out('vec4 prevwvpposition')
                vert.write('wvpposition = gl_Position;')
                vert.write('prevwvpposition = prevWVP * spos;')
            else:
                vert.add_uniform('mat4 prevW', link='_prevWorldMatrix')
                vert.add_out('vec3 prevwposition')
                vert.write('prevwposition = vec4(prevW * spos).xyz;')
                tese.add_out('vec4 wvpposition')
                tese.add_out('vec4 prevwvpposition')
                tese.add_uniform('mat4 prevVP', '_prevViewProjectionMatrix')
                tese.write('wvpposition = gl_Position;')
                make_tess.interpolate(tese, 'prevwposition', 3)
                tese.write('prevwvpposition = prevVP * vec4(prevwposition, 1.0);')
    elif gapi.startswith('direct3d'):
        vert.add_out('vec4 wvpposition')
        vert.write('wvpposition = gl_Position;')
        frag.add_out('vec4[2] fragColor')
    else:
        frag.add_out('vec4[2] fragColor')

    # Pack gbuffer
    frag.add_include('std/gbuffer.glsl')

    if mat_state.material.arm_two_sided:
        frag.write('if (!gl_FrontFacing) n *= -1;') # Flip normal when drawing back-face

    frag.write('n /= (abs(n.x) + abs(n.y) + abs(n.z));')
    frag.write('n.xy = n.z >= 0.0 ? n.xy : octahedronWrap(n.xy);')
    # TODO: store_depth
    if gapi.startswith('direct3d'):
        frag.write('fragColor[0] = vec4(n.xy, packFloat(metallic, roughness), 1.0 - ((wvpposition.z / wvpposition.w) * 0.5 + 0.5));')
    else:
        frag.write('fragColor[0] = vec4(n.xy, packFloat(metallic, roughness), 1.0 - gl_FragCoord.z);')
    frag.write('fragColor[1] = vec4(basecol.rgb, packFloat2(occlusion, specular));')

    if '_gbuffer2' in wrd.world_defs:
        if '_Veloc' in wrd.world_defs:
            frag.write('vec2 posa = (wvpposition.xy / wvpposition.w) * 0.5 + 0.5;')
            frag.write('vec2 posb = (prevwvpposition.xy / prevwvpposition.w) * 0.5 + 0.5;')
            frag.write('fragColor[2].rg = vec2(posa - posb);')
        if rpdat.arm_voxelgi_refraction:
            frag.write('fragColor[2].b = opacity;')
        if '_SSS' in wrd.world_defs or '_Hair' in wrd.world_defs:
            frag.add_uniform('int materialID')
            frag.write('fragColor[2].a = materialID;')

    return con_mesh

def make_deferred_plus(con_mesh):
    vert = con_mesh.make_vert()
    frag = con_mesh.make_frag()

    frag.add_out('vec4[3] fragColor')

    vert.add_uniform('mat3 N', '_normalMatrix')
    vert.write_attrib('vec4 spos = vec4(pos, 1.0);')

    frag.ins = vert.outs
    vert.add_uniform('mat4 WVP', '_worldViewProjectionMatrix')
    vert.write('gl_Position = WVP * spos;')

    frag.add_include('compiled.glsl')

    vert.add_out('vec2 texCoord')

    con_mesh.add_elem('tex', 2) #### Add using cycles.py
    if con_mesh.is_elem('tex'):
        vert.write_attrib('texCoord = tex;')
    else:
        vert.write_attrib('texCoord = vec2(0.0);')

    vert.add_out('vec3 wnormal')
    write_norpos(con_mesh, vert)
    frag.write_attrib('vec3 n = normalize(wnormal);')

    frag.add_uniform('float materialID', link='_objectInfoMaterialIndex')

    # Pack gbuffer
    frag.add_include('std/gbuffer.glsl')
    frag.write('n /= (abs(n.x) + abs(n.y) + abs(n.z));')
    frag.write('n.xy = n.z >= 0.0 ? n.xy : octahedronWrap(n.xy);')
    frag.write('fragColor[0] = vec4(n.xy, fract(texCoord));')
    frag.write('fragColor[1] = vec4(materialID, 0.0, 0.0, 0.0);')
    frag.write('fragColor[2] = vec4(dFdx(texCoord), dFdy(texCoord));')
    # + tangent space

def make_forward_mobile(con_mesh):
    wrd = bpy.data.worlds['Arm']
    vert = con_mesh.make_vert()
    frag = con_mesh.make_frag()
    geom = None
    tesc = None
    tese = None

    vert.add_uniform('mat3 N', '_normalMatrix')
    vert.write_attrib('vec4 spos = vec4(pos, 1.0);')
    frag.ins = vert.outs
    vert.add_uniform('mat4 WVP', '_worldViewProjectionMatrix')
    # Written in finalizer
    # vert.add_uniform('mat4 W', '_worldMatrix')
    # vert.add_out('vec3 wposition')
    # vert.write_attrib('wposition = vec4(W * spos).xyz;')
    vert.write('gl_Position = WVP * spos;')

    frag.add_include('compiled.glsl')
    frag.write('vec3 basecol;')
    frag.write('float roughness;')
    frag.write('float metallic;')
    frag.write('float occlusion;')
    frag.write('float specular;')
    cycles.parse(mat_state.nodes, con_mesh, vert, frag, geom, tesc, tese, parse_opacity=False, parse_displacement=False)

    if con_mesh.is_elem('tex'):
        vert.add_out('vec2 texCoord')
        vert.write_attrib('texCoord = tex;')

    if con_mesh.is_elem('col'):
        vert.add_out('vec3 vcolor')
        vert.write('vcolor = col;')

    vert.add_out('vec3 wnormal')
    write_norpos(con_mesh, vert)
    frag.write_attrib('vec3 n = normalize(wnormal);')

    frag.add_include('std/math.glsl')
    frag.add_include('std/brdf.glsl')
    frag.add_uniform('vec3 lightColor', '_lampColor')
    frag.add_uniform('vec3 lightDir', '_lampDirection')
    frag.add_uniform('vec3 lightPos', '_lampPosition')
    frag.add_uniform('float envmapStrength', link='_envmapStrength')

    is_shadows = not '_NoShadows' in wrd.world_defs

    frag.write('float visibility = 1.0;')
    frag.write('float dotNL = max(dot(n, lightDir), 0.0);')

    if is_shadows:
        vert.add_out('vec4 lampPos')
        vert.add_uniform('mat4 LWVP', '_biasLampWorldViewProjectionMatrix')
        vert.write('lampPos = LWVP * spos;')
        frag.add_include('std/shadows.glsl')
        frag.add_uniform('sampler2D shadowMap')
        frag.add_uniform('float shadowsBias', '_lampShadowsBias')
        frag.write('    if (lampPos.w > 0.0) {')
        frag.write('    vec3 lPos = lampPos.xyz / lampPos.w;')
        frag.write('    const float texelSize = 1.0 / shadowmapSize.x;')
        frag.write('    visibility = 0.0;')
        # TODO: CSM
        frag.write('    visibility += float(texture(shadowMap, lPos.xy).r + shadowsBias > lPos.z);')
        frag.write('    visibility += float(texture(shadowMap, lPos.xy + vec2(texelSize, 0.0)).r + shadowsBias > lPos.z) * 0.5;')
        frag.write('    visibility += float(texture(shadowMap, lPos.xy + vec2(-texelSize, 0.0)).r + shadowsBias > lPos.z) * 0.25;')
        frag.write('    visibility += float(texture(shadowMap, lPos.xy + vec2(0.0, texelSize)).r + shadowsBias > lPos.z) * 0.5;')
        frag.write('    visibility += float(texture(shadowMap, lPos.xy + vec2(0.0, -texelSize)).r + shadowsBias > lPos.z) * 0.25;')
        frag.write('    visibility /= 2.5;')
        frag.write('    visibility = max(visibility, 0.2);')
        # frag.write('    visibility = max(float(texture(shadowMap, lPos.xy).r + shadowsBias > lPos.z), 0.5);')
        frag.write('    }')

    frag.add_out('vec4 fragColor')
    blend = mat_state.material.arm_blending
    if blend:
        # frag.write('fragColor = vec4(basecol * visibility, 1.0);')
        frag.write('fragColor = vec4(basecol, 1.0);')
        return

    frag.write('vec3 direct = basecol * dotNL * lightColor;')
    # frag.write('direct += vec3(D_Approx(max(roughness, 0.3), dot(reflect(-vVec, n), lightDir)));')
    frag.write('direct *= attenuate(distance(wposition, lightPos));')

    if '_Irr' in wrd.world_defs:
        frag.add_include('std/shirr.glsl')
        frag.add_uniform('vec4 shirr[7]', link='_envmapIrradiance', included=True)
        env_str = 'shIrradiance(n)'
    else:
        env_str = '0.5'
    frag.write('fragColor = vec4(direct * visibility + basecol * {0} * envmapStrength, 1.0);'.format(env_str))

    if '_LDR' in wrd.world_defs:
        frag.write('fragColor.rgb = pow(fragColor.rgb, vec3(1.0 / 2.2));')

def make_forward_solid(con_mesh):
    wrd = bpy.data.worlds['Arm']
    vert = con_mesh.make_vert()
    frag = con_mesh.make_frag()
    geom = None
    tesc = None
    tese = None

    vert.write_attrib('vec4 spos = vec4(pos, 1.0);')
    frag.ins = vert.outs
    vert.add_uniform('mat4 WVP', '_worldViewProjectionMatrix')
    vert.write('gl_Position = WVP * spos;')

    frag.add_include('compiled.glsl')
    frag.write('vec3 basecol;')
    frag.write('float roughness;')
    frag.write('float metallic;')
    frag.write('float occlusion;')
    frag.write('float specular;')
    cycles.parse(mat_state.nodes, con_mesh, vert, frag, geom, tesc, tese, parse_opacity=False, parse_displacement=False, basecol_only=True)

    if con_mesh.is_elem('tex'):
        vert.add_out('vec2 texCoord')
        vert.write_attrib('texCoord = tex;')

    if con_mesh.is_elem('col'):
        vert.add_out('vec3 vcolor')
        vert.write('vcolor = col;')

    write_norpos(con_mesh, vert, write_nor=False)

    frag.add_out('vec4 fragColor')
    frag.write('fragColor = vec4(basecol, 1.0);')

    if '_LDR' in wrd.world_defs:
        frag.write('fragColor.rgb = pow(fragColor.rgb, vec3(1.0 / 2.2));')

    if arm.utils.get_gapi().startswith('direct3d'):
        vert.add_out('vec3 normal') # Prevent discard
        vert.write('normal = nor;')

def make_forward(con_mesh):
    wrd = bpy.data.worlds['Arm']
    make_forward_base(con_mesh)

    frag = con_mesh.frag

    blend = mat_state.material.arm_blending
    if not blend:
        frag.add_out('vec4 fragColor')
        frag.write('fragColor = vec4(direct * lightColor * visibility + indirect * occlusion, 1.0);')
    
        if '_LDR' in wrd.world_defs:
            frag.add_include('std/tonemap.glsl')
            frag.write('fragColor.rgb = tonemapFilmic(fragColor.rgb);')
            # frag.write('fragColor.rgb = pow(fragColor.rgb, vec3(1.0 / 2.2));')

    # Particle opacity
    if mat_state.material.arm_particle == 'gpu' and mat_state.material.arm_particle_fade:
        frag.write('fragColor.rgb *= p_fade;')

def make_forward_base(con_mesh, parse_opacity=False):
    global is_displacement
    wrd = bpy.data.worlds['Arm']

    arm_discard = mat_state.material.arm_discard
    make_base(con_mesh, parse_opacity=(parse_opacity or arm_discard))

    vert = con_mesh.vert
    frag = con_mesh.frag
    tese = con_mesh.tese

    if arm_discard:
        opac = mat_state.material.arm_discard_opacity
        frag.write('if (opacity < {0}) discard;'.format(opac))

    frag.write_init("""
    vec3 vVec = normalize(eyeDir);
    float dotNV = max(dot(n, vVec), 0.0);
""")

    sh = tese if tese != None else vert
    sh.add_out('vec3 eyeDir')
    sh.add_uniform('vec3 eye', '_cameraPosition')
    sh.write('eyeDir = eye - wposition;')

    frag.add_include('std/brdf.glsl')
    frag.add_include('std/math.glsl')
    frag.add_uniform('vec3 lightColor', '_lampColor')
    frag.add_uniform('vec3 lightDir', '_lampDirection')
    frag.add_uniform('vec3 lightPos', '_lampPosition')
    frag.add_uniform('int lightType', '_lampType')
    frag.add_uniform('vec2 spotlightData', '_spotlampData') # cutoff, cutoff - exponent
    frag.add_uniform('float envmapStrength', link='_envmapStrength')

    if '_Brdf' in wrd.world_defs:
        frag.add_uniform('sampler2D senvmapBrdf', link='_envmapBrdf')
    if '_Irr' in wrd.world_defs:
        frag.add_include('std/shirr.glsl')
        frag.add_uniform('vec4 shirr[7]', link='_envmapIrradiance', included=True)
        if '_Rad' in wrd.world_defs:
            frag.add_uniform('sampler2D senvmapRadiance', link='_envmapRadiance')
            frag.add_uniform('int envmapNumMipmaps', link='_envmapNumMipmaps')

    is_shadows = not '_NoShadows' in wrd.world_defs

    frag.write('float visibility = 1.0;')
    frag.write('vec3 lp = lightPos - wposition;')
    frag.write('vec3 l;')
    frag.write('if (lightType == 0) l = lightDir;')
    frag.write('else { l = normalize(lp); visibility *= attenuate(distance(wposition, lightPos)); }')
    frag.write('vec3 h = normalize(vVec + l);')
    frag.write('float dotNL = dot(n, l);')
    frag.write('float dotNH = dot(n, h);')
    frag.write('float dotVH = dot(vVec, h);')

    if is_shadows:
        if tese != None:
            tese.add_out('vec4 lampPos')
            tese.add_uniform('mat4 LVP', '_biasLampViewProjectionMatrix')
            tese.add_uniform('int lightShadow', '_lampCastShadow')
            tese.write('if (lightShadow == 1) lampPos = LVP * vec4(wposition, 1.0);')
        else:
            if is_displacement:
                vert.add_out('vec4 lampPos')
                vert.add_uniform('mat4 LVP', '_biasLampViewProjectionMatrix')
                vert.add_uniform('int lightShadow', '_lampCastShadow')
                vert.write('if (lightShadow == 1) lampPos = LVP * vec4(wposition, 1.0);')
            else:
                vert.add_out('vec4 lampPos')
                vert.add_uniform('int lightShadow', '_lampCastShadow')
                vert.add_uniform('mat4 LWVP', '_biasLampWorldViewProjectionMatrix')
                vert.write('if (lightShadow == 1) lampPos = LWVP * spos;')
        
        frag.add_include('std/shadows.glsl')
        frag.add_uniform('sampler2D shadowMap')
        frag.add_uniform('samplerCube shadowMapCube')
        frag.add_uniform('bool receiveShadow')
        frag.add_uniform('float shadowsBias', '_lampShadowsBias')
        frag.add_uniform('int lightShadow', '_lampCastShadow')
        frag.add_uniform('vec2 lightProj', '_lampPlaneProj')

        frag.write('if (receiveShadow) {')
        frag.write('    if (lightShadow == 1) {')
        if '_CSM' in wrd.world_defs:
            frag.add_include('compiled.glsl')
            frag.add_uniform('vec4 casData[shadowmapCascades * 4 + 4]', '_cascadeData', included=True)
            frag.add_uniform('vec3 eye', '_cameraPosition')
            frag.write('vec2 smSize;')
            frag.write('vec3 lPos;')
            frag.write('if (lightType == 0) {')
            frag.write('    int casi;')
            frag.write('    int casindex;')
            frag.write('    mat4 LWVP = getCascadeMat(distance(eye, wposition), casi, casindex);')
            frag.write('    vec4 lampPos = LWVP * vec4(wposition, 1.0);')
            frag.write('    lPos = lampPos.xyz / lampPos.w;')
            frag.write('    smSize = shadowmapSize * vec2(shadowmapCascades, 1.0);')
            frag.write('}')
            frag.write('else {')
            frag.write('    lPos = lampPos.xyz / lampPos.w;')
            frag.write('    smSize = shadowmapSize;')
            frag.write('}')
        else:
            frag.write('    vec3 lPos = lampPos.xyz / lampPos.w;')
            frag.write('    const vec2 smSize = shadowmapSize;')
        # frag.write('float bias = clamp(shadowsBias * 1.0 * tan(acos(clamp(dotNL, 0.0, 1.0))), 0.0, 0.01);')
        frag.write('    visibility *= PCF(shadowMap, lPos.xy, lPos.z - shadowsBias, smSize);')
        frag.write('    }')
        frag.write('    else if (lightShadow == 2) visibility *= PCFCube(shadowMapCube, lp, -l, shadowsBias, lightProj, n);')
        frag.write('}')

    frag.write('if (lightType == 2) {')
    frag.write('    float spotEffect = dot(lightDir, l);')
    frag.write('    if (spotEffect < spotlightData.x) {')
    frag.write('        visibility *= smoothstep(spotlightData.y, spotlightData.x, spotEffect);')
    frag.write('    }')
    frag.write('}')

    blend = mat_state.material.arm_blending
    if blend:
        frag.add_out('vec4 fragColor')
        # frag.write('fragColor = vec4(basecol * lightColor * visibility, 1.0);')
        frag.write('fragColor = vec4(basecol, 1.0);')
        # TODO: Fade out fragments near depth buffer here
        return

    frag.write('vec3 albedo = surfaceAlbedo(basecol, metallic);')
    frag.write('vec3 f0 = surfaceF0(basecol, metallic);')
    frag.write('vec3 direct;')

    if '_LTC' in wrd.world_defs:
        frag.add_include('std/ltc.glsl')
        frag.add_uniform('sampler2D sltcMat', link='_ltcMat')
        frag.add_uniform('sampler2D sltcMag', link='_ltcMag')
        frag.add_uniform('vec3 lampArea0', link='_lampArea0')
        frag.add_uniform('vec3 lampArea1', link='_lampArea1')
        frag.add_uniform('vec3 lampArea2', link='_lampArea2')
        frag.add_uniform('vec3 lampArea3', link='_lampArea3')
        frag.write('if (lightType == 3) {')
        frag.write('    float theta = acos(dotNV);')
        frag.write('    vec2 tuv = vec2(roughness, theta / (0.5 * PI));')
        frag.write('    tuv = tuv * LUT_SCALE + LUT_BIAS;')
        frag.write('    vec4 t = texture(sltcMat, tuv);')
        frag.write('    mat3 invM = mat3(vec3(1.0, 0.0, t.y), vec3(0.0, t.z, 0.0), vec3(t.w, 0.0, t.x));')
        frag.write('    float ltcspec = ltcEvaluate(n, vVec, dotNV, wposition, invM, lampArea0, lampArea1, lampArea2, lampArea3);')
        frag.write('    ltcspec *= texture(sltcMag, tuv).a;')
        frag.write('    float ltcdiff = ltcEvaluate(n, vVec, dotNV, wposition, mat3(1.0), lampArea0, lampArea1, lampArea2, lampArea3);')
        frag.write('    direct = albedo * ltcdiff + ltcspec * specular;')
        frag.write('}')
        frag.write('else {')
        frag.tab += 1

    frag.write('direct = lambertDiffuseBRDF(albedo, dotNL);')
    frag.write('direct += specularBRDF(f0, roughness, dotNL, dotNH, dotNV, dotVH) * specular;')

    if '_LTC' in wrd.world_defs:
        frag.write('}')
        frag.tab -= 1

    if '_Brdf' in wrd.world_defs:
        frag.write('vec2 envBRDF = texture(senvmapBrdf, vec2(roughness, 1.0 - dotNV)).xy;')

    if '_Irr' in wrd.world_defs:
        frag.write('vec3 indirect = shIrradiance(n);')
        if '_EnvTex' in wrd.world_defs:
            frag.write('indirect /= PI;')
        frag.write('indirect *= albedo;')

        if '_Rad' in wrd.world_defs:
            frag.write('vec3 reflectionWorld = reflect(-vVec, n);')
            frag.write('float lod = getMipFromRoughness(roughness, envmapNumMipmaps);')
            frag.write('vec3 prefilteredColor = textureLod(senvmapRadiance, envMapEquirect(reflectionWorld), lod).rgb;')
            if '_EnvLDR' in wrd.world_defs:
                frag.write('prefilteredColor = pow(prefilteredColor, vec3(2.2));')
            frag.write('indirect += prefilteredColor * (f0 * envBRDF.x + envBRDF.y) * 1.5;')
        elif '_EnvCol' in wrd.world_defs:
            frag.add_uniform('vec3 backgroundCol', link='_backgroundCol')
            frag.write('indirect += backgroundCol * f0;')
    else:
        frag.write('vec3 indirect = albedo;')
    frag.write('indirect *= envmapStrength;')

    if '_VoxelGI' in wrd.world_defs or '_VoxelAO' in wrd.world_defs:
        frag.add_include('std/conetrace.glsl')
        frag.add_uniform('sampler3D voxels')
        if '_VoxelGICam' in wrd.world_defs:
            frag.add_uniform('vec3 eyeSnap', link='_cameraPositionSnap')
            frag.write('vec3 voxpos = (wposition - eyeSnap) / voxelgiHalfExtents;')
        else:
            frag.write('vec3 voxpos = wposition / voxelgiHalfExtents;')
        if '_VoxelAO' in wrd.world_defs:
            frag.write('indirect *= vec3(1.0 - traceAO(voxpos, n, voxels));')
            # frag.write('indirect = vec3(1.0 - traceAO(voxpos, n, voxels));') # AO view
        else:
            frag.write('vec4 indirectDiffuse = traceDiffuse(voxpos, n, voxels);')
            frag.write('indirect = indirect * voxelgiEnv + vec3(indirectDiffuse.rgb * voxelgiDiff * basecol);')
            frag.write('if (specular > 0.0) {')
            frag.write('vec3 indirectSpecular = traceSpecular(voxels, voxpos, n, vVec, roughness);')
            frag.write('indirectSpecular *= f0 * envBRDF.x + envBRDF.y;')
            frag.write('indirect += indirectSpecular * voxelgiSpec * specular;')
            frag.write('}')
